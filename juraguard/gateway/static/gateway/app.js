const replacementUrl = document.querySelector("[data-replace-url]")?.dataset.replaceUrl;
if (replacementUrl) window.history.replaceState(null, "", replacementUrl);

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const value = button.parentElement.querySelector("code")?.textContent;
    if (!value) return;
    await navigator.clipboard.writeText(value.trim());
    const label = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(() => { button.textContent = label; }, 1400);
  });
});

const integrationForm = document.querySelector("[data-integration-form]");
if (integrationForm) {
  const provider = integrationForm.querySelector("[name=provider_type]");
  const updateFields = () => integrationForm.querySelectorAll("[data-provider-fields]").forEach((field) => {
    field.hidden = !field.dataset.providerFields.split(" ").includes(provider.value);
  });
  provider.addEventListener("change", updateFields);
  updateFields();
}
