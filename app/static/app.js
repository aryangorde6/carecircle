// CareCircle: small progressive enhancements. Every feature works without JavaScript.
(function () {
  "use strict";

  // Text size toggle. Only a display preference is stored, never personal data.
  const root = document.documentElement;
  const saved = window.localStorage.getItem("cc-text-size");
  function applySize(size) {
    root.dataset.textSize = size;
    document.querySelectorAll("[data-text-size]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.textSize === size));
    });
  }
  if (saved === "large" || saved === "normal") applySize(saved);

  document.addEventListener("click", function (event) {
    const sizeBtn = event.target.closest("[data-text-size]");
    if (sizeBtn) {
      applySize(sizeBtn.dataset.textSize);
      window.localStorage.setItem("cc-text-size", sizeBtn.dataset.textSize);
      return;
    }

    // Read aloud with the browser's built-in speech synthesis.
    const speakBtn = event.target.closest("[data-speak]");
    if (speakBtn && "speechSynthesis" in window) {
      const target = document.querySelector(speakBtn.dataset.speak);
      if (!target) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(target.textContent.replace(/\s+/g, " ").trim());
      utterance.rate = 0.9;
      window.speechSynthesis.speak(utterance);
    }
  });

  // Hide the read-aloud button where speech is not supported.
  if (!("speechSynthesis" in window)) {
    document.querySelectorAll("[data-speak]").forEach(function (btn) { btn.hidden = true; });
  }

  // Show progress on slow AI submissions and prevent double submits.
  document.addEventListener("submit", function (event) {
    const button = event.submitter;
    if (button && button.dataset.busyText) {
      button.disabled = true;
      button.textContent = button.dataset.busyText;
      button.setAttribute("aria-busy", "true");
    }
  });
})();
