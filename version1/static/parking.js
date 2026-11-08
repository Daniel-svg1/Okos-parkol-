const spaces = [...document.querySelectorAll('[data-space]')];
const bookingButton = document.querySelector('#open-booking');
const dialog = document.querySelector('#working-dialog');

function showNotice() {
  dialog.showModal();
}

document.querySelectorAll('[data-placeholder]').forEach(button => {
  button.addEventListener('click', showNotice);
});
document.querySelectorAll('.close-dialog').forEach(button => {
  button.addEventListener('click', () => dialog.close());
});
dialog.addEventListener('click', event => {
  if (event.target === dialog) {
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  }
});

function selectSpace(space) {
  spaces.forEach(button => {
    const selected = button === space;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  document.querySelectorAll('[data-selected-space]').forEach(label => {
    label.textContent = space.dataset.space;
  });
  const status = space.dataset.status;
  const badge = document.querySelector('#selected-status');
  badge.className = `status-badge ${status}`;
  badge.querySelector('span').textContent = status[0].toUpperCase() + status.slice(1);
  document.querySelector('#selected-description').textContent = status === 'available'
    ? 'Available to book' : status === 'occupied' ? 'Currently occupied' : 'Already reserved';
  const available = status === 'available';
  bookingButton.disabled = !available;
  document.querySelector('#selection-note').textContent = available
    ? 'Booking is coming soon.'
    : 'Choose an available space to continue.';
}
spaces.forEach(space => space.addEventListener('click', () => selectSpace(space)));

document.querySelector('[data-display-date]').textContent = new Date().toLocaleDateString('en-GB', {
  day: 'numeric', month: 'long', year: 'numeric'
});
selectSpace(document.querySelector('[data-space="A3"]'));
