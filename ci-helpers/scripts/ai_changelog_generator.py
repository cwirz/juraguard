#!/usr/bin/env python3
"""
AI-powered changelog generator using Claude AI.
Analyzes git commits and diffs to generate human-readable changelog entries.
"""

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

import anthropic
from git import Commit, Repo

# Project context to help Claude understand the codebase
PROJECT_CONTEXT = """
Project Context:
- Full-stack monorepo
- Backend: Django 5 + Django REST Framework (Python 3.11)
- Frontend: Nuxt 4 + Vue 3 + TypeScript + Tailwind CSS 4
- Key directories:
  * backend/ - Django REST API
  * website/ - Nuxt frontend
  * ci-helpers/ - CI/CD scripts
  * database/ - PostgreSQL setup
  * documentation/ - Sphinx docs
"""


def get_commits_in_range(repo_path: str, before_sha: str, after_sha: str) -> List[Commit]:
    """
    Get all commits between two SHAs using GitPython.
    
    Args:
        repo_path: Path to the git repository
        before_sha: Starting commit SHA (exclusive)
        after_sha: Ending commit SHA (inclusive)
    
    Returns:
        List of Commit objects
    """
    try:
        repo = Repo(repo_path)

        # Handle initial commit case where before_sha might be all zeros
        if before_sha == '0000000000000000000000000000000000000000' or not before_sha:
            # First push to the branch - get just the latest commit
            commits = [repo.commit(after_sha)]
        else:
            # Get commits in range: before_sha..after_sha
            commit_range = f"{before_sha}..{after_sha}"
            commits = list(repo.iter_commits(commit_range))

        return commits
    except Exception as e:
        print(f"Error getting commits: {e}", file=sys.stderr)
        return []


def count_diff_lines(diff_text: str) -> Tuple[int, int]:
    """
    Count added and removed lines in a diff.
    
    Returns:
        Tuple of (lines_added, lines_removed)
    """
    added = 0
    removed = 0

    for line in diff_text.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1

    return added, removed


def filter_large_files(commit: Commit, max_lines: int = 500) -> Dict[str, any]:
    """
    Extract diff information and filter out large files (>max_lines changed).
    
    Args:
        commit: GitPython Commit object
        max_lines: Maximum lines changed to include full diff
    
    Returns:
        Dictionary with diff information and large file notices
    """
    result = {
        'included_diffs': [],
        'large_files': [],
        'total_files': 0,
        'total_additions': 0,
        'total_deletions': 0
    }

    try:
        # Get diff from parent (or empty tree for first commit)
        if commit.parents:
            diffs = commit.parents[0].diff(commit, create_patch=True)
        else:
            diffs = commit.diff(None, create_patch=True)

        for diff_item in diffs:
            result['total_files'] += 1

            # Get file path
            file_path = diff_item.b_path if diff_item.b_path else diff_item.a_path

            # Get the diff text
            diff_text = ""
            if diff_item.diff:
                try:
                    diff_text = diff_item.diff.decode('utf-8', errors='ignore')
                except:
                    diff_text = str(diff_item.diff)

            # Count lines changed
            added, removed = count_diff_lines(diff_text)
            result['total_additions'] += added
            result['total_deletions'] += removed

            total_changed = added + removed

            if total_changed > max_lines:
                # Large file - just note it
                result['large_files'].append({
                    'path': file_path,
                    'additions': added,
                    'deletions': removed,
                    'total_changed': total_changed
                })
            else:
                # Include full diff
                result['included_diffs'].append({
                    'path': file_path,
                    'diff': diff_text,
                    'additions': added,
                    'deletions': removed
                })

    except Exception as e:
        print(f"Error processing diff: {e}", file=sys.stderr)

    return result


def get_commit_details(commit: Commit) -> Dict[str, any]:
    """
    Extract detailed information from a commit.
    
    Returns:
        Dictionary with commit details
    """
    return {
        'sha': commit.hexsha[:8],
        'full_sha': commit.hexsha,
        'message': commit.message.strip(),
        'author': str(commit.author),
        'date': commit.committed_datetime.isoformat(),
        'summary': commit.summary
    }


def build_claude_prompt(commits_data: List[Dict], release_type: str) -> str:
    """
    Build the prompt for Claude based on commits and release type.
    
    Args:
        commits_data: List of commit information dictionaries
        release_type: 'minor', 'major', or 'public'
    
    Returns:
        Formatted prompt string
    """
    # Start with project context
    prompt = PROJECT_CONTEXT + "\n\n"

    # Add commits summary
    prompt += f"Number of commits in this release: {len(commits_data)}\n\n"

    # Add detailed commit information
    prompt += "Commits:\n"
    for i, commit_info in enumerate(commits_data, 1):
        prompt += f"\n{i}. Commit {commit_info['details']['sha']}\n"
        prompt += f"   Author: {commit_info['details']['author']}\n"
        prompt += f"   Date: {commit_info['details']['date']}\n"
        prompt += f"   Message: {commit_info['details']['message']}\n"

        diff_info = commit_info['diff']
        prompt += f"   Files changed: {diff_info['total_files']}, "
        prompt += f"+{diff_info['total_additions']} -{diff_info['total_deletions']}\n"

        # Add large files notice
        if diff_info['large_files']:
            prompt += f"   Large files (>500 lines, excluded from diff):\n"
            for lf in diff_info['large_files']:
                prompt += f"     - {lf['path']}: +{lf['additions']} -{lf['deletions']}\n"

        # Add included diffs
        if diff_info['included_diffs']:
            prompt += f"   Changed files (with diffs):\n"
            for df in diff_info['included_diffs'][:10]:  # Limit to first 10 files
                prompt += f"\n     File: {df['path']}\n"
                # Truncate very long diffs
                diff_lines = df['diff'].split('\n')
                if len(diff_lines) > 100:
                    prompt += '\n'.join(diff_lines[:100])
                    prompt += f"\n     ... (diff truncated, {len(diff_lines) - 100} more lines)\n"
                else:
                    prompt += df['diff'] + "\n"

    # Add release-type-specific instructions
    prompt += "\n\n---\n\n"

    if release_type in ['major', 'public']:
        prompt += """Generate a structured changelog with these sections (only include sections that have content):

### Features
- New functionality added

### Improvements
- Enhancements to existing features

### Bug Fixes
- Issues resolved

### Breaking Changes
- Changes that require user action

Guidelines:
- Be concise but comprehensive
- Focus on user-facing changes and business value
- Use clear, non-technical language where possible
- Each bullet point should be a complete sentence
- Don't include implementation details unless necessary
- Group related changes together
- Highlight breaking changes clearly

Generate ONLY the changelog content (sections with bullet points). Do NOT include the release header or separator lines."""
    else:
        # Minor release
        prompt += """Generate a concise changelog entry (1-3 sentences) summarizing what changed.

Guidelines:
- Focus on the most important user-facing changes
- Be clear and concise
- Use plain language
- Combine multiple small changes into a coherent summary
- Don't include technical implementation details

Generate ONLY the summary text, nothing else."""

    return prompt


def call_claude_api(prompt: str, api_key: str, max_tokens: int = 2000) -> str:
    """
    Call the Claude API to generate changelog content.
    
    Args:
        prompt: The prompt to send to Claude
        api_key: Anthropic API key
        max_tokens: Maximum tokens for response
    
    Returns:
        Generated changelog text
    
    Raises:
        Exception: If API call fails
    """
    try:
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        # Extract text from response
        response_text = message.content[0].text.strip()
        return response_text

    except anthropic.APIError as e:
        print(f"Claude API error: {e}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"Error calling Claude API: {e}", file=sys.stderr)
        raise


def fallback_to_simple(commit_message: str) -> str:
    """
    Fallback to simple changelog generation from commit message.
    This replicates the old bash script behavior.
    
    Args:
        commit_message: The commit message to use
    
    Returns:
        Simple changelog text
    """
    # Clean up commit message
    message = commit_message.strip()

    # Remove release markers
    message = message.replace('#major_release', '').replace('#public_release', '')
    message = message.strip()

    # Take first line only for minor releases
    first_line = message.split('\n')[0].strip()

    return first_line


def format_minor_release(content: str) -> str:
    """
    Format changelog content for minor release (simple one-line format).
    """
    # Clean up any extra whitespace or newlines
    content = ' '.join(content.split())
    return content


def format_major_release(content: str) -> str:
    """
    Format changelog content for major/public release (structured format).
    Ensures proper formatting with newlines.
    """
    # Ensure proper spacing around headers
    content = re.sub(r'###\s*', '\n### ', content)
    content = re.sub(r'\n\n\n+', '\n\n', content)
    content = content.strip()
    return content


def main():
    parser = argparse.ArgumentParser(description='Generate AI-powered changelog entries')
    parser.add_argument('--release-type', required=True, choices=['minor', 'major', 'public'],
                        help='Type of release')
    parser.add_argument('--tag', required=True, help='Release tag (e.g., v0.0.1)')
    parser.add_argument('--message', required=True, help='Commit message')
    parser.add_argument('--before-sha', required=True, help='Starting commit SHA')
    parser.add_argument('--after-sha', required=True, help='Ending commit SHA')
    parser.add_argument('--repo-path', default='.', help='Path to git repository')

    args = parser.parse_args()

    # Get API key from environment
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        print("WARNING: ANTHROPIC_API_KEY not found, falling back to simple changelog",
              file=sys.stderr)
        output = fallback_to_simple(args.message)
        print(output)
        return 0

    try:
        # Get commits in range
        commits = get_commits_in_range(args.repo_path, args.before_sha, args.after_sha)

        if not commits:
            print("WARNING: No commits found, falling back to simple changelog",
                  file=sys.stderr)
            output = fallback_to_simple(args.message)
            print(output)
            return 0

        # Gather detailed information for each commit
        commits_data = []
        for commit in commits:
            commit_details = get_commit_details(commit)
            diff_info = filter_large_files(commit, max_lines=500)

            commits_data.append({
                'details': commit_details,
                'diff': diff_info
            })

        # Build prompt for Claude
        prompt = build_claude_prompt(commits_data, args.release_type)

        # Call Claude API
        print(f"Calling Claude API to generate {args.release_type} release changelog...",
              file=sys.stderr)
        changelog_content = call_claude_api(prompt, api_key)

        # Format based on release type
        if args.release_type in ['major', 'public']:
            formatted_output = format_major_release(changelog_content)
        else:
            formatted_output = format_minor_release(changelog_content)

        # Output the result (this will be captured by bash script)
        print(formatted_output)
        return 0

    except Exception as e:
        print(f"ERROR: Failed to generate AI changelog: {e}", file=sys.stderr)
        print("Falling back to simple changelog generation", file=sys.stderr)
        output = fallback_to_simple(args.message)
        print(output)
        return 0


if __name__ == '__main__':
    sys.exit(main())
