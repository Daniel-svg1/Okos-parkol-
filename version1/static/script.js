const form = document.getElementById("sign-in-form");
const message = document.getElementById("preview-message");

// Ez meg csak egy feluletterv: a gombok ugyanazt az uzenetet mutatjak.
function showPreviewMessage() {
  message.textContent = "Working on it";
  message.hidden = false;
}

form.addEventListener("submit", function (event) {
  event.preventDefault();
  showPreviewMessage();
});

document.querySelectorAll("[data-preview]").forEach(function (button) {
  button.addEventListener("click", showPreviewMessage);
});
