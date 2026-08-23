document.addEventListener("click", (event) => {
  const openButton = event.target.closest("[data-modal-open]");
  if (openButton) {
    document.getElementById(openButton.dataset.modalOpen)?.showModal();
  }
  if (event.target.closest("[data-modal-close]")) {
    event.target.closest("dialog")?.close();
  }
});

const menuButton = document.getElementById("menu-toggle");
const sidebar = document.getElementById("sidebar");
menuButton?.addEventListener("click", () => {
  const isOpen = sidebar.classList.toggle("open");
  menuButton.setAttribute("aria-expanded", String(isOpen));
});

document.querySelectorAll('input[type="date"]').forEach((input) => {
  if (!input.value) {
    const due = new Date();
    due.setDate(due.getDate() + 7);
    input.value = due.toISOString().slice(0, 10);
  }
});

