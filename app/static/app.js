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

    // Print the visit sheet (the button is hidden until JavaScript is available).
    if (event.target.closest("[data-print]")) {
      window.print();
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
      // Read Hindi, Tamil, etc. with a matching voice when the text is marked with lang.
      const lang = target.closest("[lang]");
      if (lang) utterance.lang = lang.getAttribute("lang");
      window.speechSynthesis.speak(utterance);
    }
  });

  document.querySelectorAll("[data-print]").forEach(function (btn) { btn.hidden = false; });

  // Hide the read-aloud button where speech is not supported.
  if (!("speechSynthesis" in window)) {
    document.querySelectorAll("[data-speak]").forEach(function (btn) { btn.hidden = true; });
  }

  // Voice notes: record in the browser, then send it through the same form as a file,
  // so the server sees exactly what the no-JavaScript file picker would send.
  const voiceForm = document.querySelector("[data-voice]");
  if (voiceForm && window.MediaRecorder && navigator.mediaDevices && window.DataTransfer) {
    const controls = voiceForm.querySelector("[data-voice-controls]");
    const fileBox = voiceForm.querySelector("[data-voice-file]");
    const startBtn = voiceForm.querySelector("[data-voice-start]");
    const stopBtn = voiceForm.querySelector("[data-voice-stop]");
    const status = voiceForm.querySelector("[data-voice-status]");
    const input = voiceForm.querySelector('input[type="file"]');
    const MAX_SECONDS = 45;
    let recorder = null;
    let timer = null;

    controls.hidden = false;
    fileBox.hidden = true;
    input.required = false;

    function useFilePicker(message) {
      status.textContent = message;
      startBtn.hidden = true;
      stopBtn.hidden = true;
      fileBox.hidden = false;
      input.required = true;
    }

    startBtn.addEventListener("click", function () {
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        const chunks = [];
        let seconds = 0;
        recorder = new MediaRecorder(stream);
        recorder.addEventListener("dataavailable", function (event) {
          if (event.data.size) chunks.push(event.data);
        });
        recorder.addEventListener("stop", function () {
          stream.getTracks().forEach(function (track) { track.stop(); });
          window.clearInterval(timer);
          stopBtn.classList.remove("recording");
          stopBtn.disabled = true;
          const type = (recorder.mimeType || "audio/webm").split(";")[0];
          const ext = type.indexOf("mp4") >= 0 ? "m4a" : type.indexOf("ogg") >= 0 ? "ogg" : "webm";
          const transfer = new DataTransfer();
          transfer.items.add(new File(chunks, "voice." + ext, { type: type }));
          input.files = transfer.files;
          status.textContent = "Listening to your voice note…";
          voiceForm.submit();
        });
        recorder.start();
        startBtn.hidden = true;
        stopBtn.hidden = false;
        stopBtn.classList.add("recording");
        stopBtn.focus();
        status.textContent = "Recording. Speak now, then press Stop.";
        timer = window.setInterval(function () {
          seconds += 1;
          status.textContent = "Recording: " + seconds + " s. Press Stop when you are done.";
          if (seconds >= MAX_SECONDS && recorder.state === "recording") recorder.stop();
        }, 1000);
      }).catch(function () {
        useFilePicker("The microphone is not available. You can choose a voice note instead.");
      });
    });

    stopBtn.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") recorder.stop();
    });
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
