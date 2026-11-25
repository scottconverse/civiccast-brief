// A button with data-confirm asks first. Capture phase, so it runs before the busy handler below
// and a cancelled form is never marked as sent.
document.addEventListener("submit", function (event) {
  var form = event.target;
  var button = event.submitter || form.querySelector("button[type=submit]");
  if (button && button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
    event.preventDefault();
    event.stopPropagation();
  }
}, true);

// Shows that a pressed button is working: it is disabled and says what it is doing until the
// next page loads. Every action also ends on a page that says what happened.
document.addEventListener("submit", function (event) {
  var form = event.target;
  var button = event.submitter || form.querySelector("button[type=submit]");
  if (!button) return;
  if (form.dataset.sent === "1") { event.preventDefault(); return; }
  form.dataset.sent = "1";
  form.setAttribute("aria-busy", "true");
  button.dataset.label = button.textContent;
  button.textContent = button.dataset.busy || "Working…";
  button.classList.add("busy");
  // Disable after the browser has read the form, so the button's own value is still sent.
  setTimeout(function () { button.disabled = true; }, 0);
  var status = document.getElementById("busy-status");
  if (status) status.textContent = button.textContent;
});

// Sources: show only the fields for the chosen kind.
function showKindFields() {
  var select = document.getElementById("kind");
  if (!select) return;
  document.querySelectorAll("[data-kind]").forEach(function (block) {
    block.hidden = block.dataset.kind !== select.value;
  });
}
document.addEventListener("change", function (event) {
  if (event.target && event.target.id === "kind") showKindFields();
});

// Voices: show only the voice fields for the chosen engine. Without the script every engine's
// fields are shown, and the form still saves what was changed.
function showVoiceFields() {
  var select = document.getElementById("engine");
  if (!select) return;
  document.querySelectorAll("[data-voice-engine]").forEach(function (block) {
    block.hidden = block.dataset.voiceEngine !== select.value;
  });
}
document.addEventListener("change", function (event) {
  if (event.target && event.target.id === "engine") showVoiceFields();
});

// A page restored from the back/forward cache gets its buttons back.
window.addEventListener("pageshow", function () {
  document.querySelectorAll("form[data-sent]").forEach(function (form) {
    delete form.dataset.sent;
    form.removeAttribute("aria-busy");
  });
  document.querySelectorAll("button.busy").forEach(function (button) {
    button.disabled = false;
    button.textContent = button.dataset.label || button.textContent;
    button.classList.remove("busy");
  });
  var status = document.getElementById("busy-status");
  if (status) status.textContent = "";
  showKindFields();
  showVoiceFields();
});
