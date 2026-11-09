const occupyButton = document.querySelector('#admin-occupy');
const reserveButton = document.querySelector('#admin-reserve');
const freeButton = document.querySelector('#admin-free');
const startInput = document.querySelector('#reserve-start');
const endInput = document.querySelector('#reserve-end');
const midnightInput = document.querySelector('#reserve-midnight');
const adminMessage = document.querySelector('#admin-message');
const carSource = document.querySelector('#car-source');
const carExit = document.querySelector('#car-exit');
let adminBusy = false;
let draggedCar = null;
let scheduleDate = null;
let inputDate = null;
let serverOffset = 0;

function parkingClock(instant = new Date(Date.now() + serverOffset)) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(instant).map(part => [part.type, part.value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
}

function reservationRangeValid() {
  const clock = parkingClock();
  const end = midnightInput.checked ? '24:00' : endInput.value;
  return scheduleDate === clock.date && Boolean(startInput.value && end)
    && startInput.value >= clock.time && end > startInput.value && end <= '24:00';
}

function syncTimeInputs(disabled) {
  const clock = parkingClock();
  if (scheduleDate && inputDate !== scheduleDate) {
    inputDate = scheduleDate;
    startInput.value = clock.time;
    const [hour, minute] = clock.time.split(':').map(Number);
    const ending = Math.min(1440, hour * 60 + minute + 60);
    midnightInput.checked = ending === 1440;
    endInput.value = ending === 1440 ? '23:59'
      : `${String(Math.floor(ending / 60)).padStart(2, '0')}:${String(ending % 60).padStart(2, '0')}`;
  }
  startInput.min = clock.time;
  startInput.max = '23:59';
  endInput.min = startInput.value;
  endInput.max = '23:59';
  startInput.disabled = disabled;
  endInput.disabled = disabled || midnightInput.checked;
  midnightInput.disabled = disabled;
  endInput.hidden = midnightInput.checked;
  document.querySelector('#midnight-value').hidden = !midnightInput.checked;
}

function renderReservations(selected, disabled) {
  const list = document.querySelector('#reservation-list');
  list.replaceChildren();
  const bookings = selected ? selected.reservations || [] : [];
  if (!bookings.length) return;
  list.append(element('p', 'reservation-list-title', 'Reservations today'));
  for (const booking of bookings) {
    const start = parkingClock(new Date(booking.start_time));
    const end = parkingClock(new Date(booking.end_time));
    const row = element('div', 'reservation-row');
    const times = element('div', '');
    times.append(element('strong', '', `${start.time} – ${end.date !== scheduleDate ? '24:00' : end.time}`));
    times.append(element('span', '', Date.parse(booking.start_time) <= Date.now() + serverOffset ? 'Active now' : 'Later today'));
    const cancel = element('button', 'cancel-reservation', 'Cancel');
    cancel.type = 'button';
    cancel.disabled = disabled;
    cancel.setAttribute('aria-label', `Cancel reservation ${start.time} to ${end.date !== scheduleDate ? 'midnight' : end.time}`);
    cancel.addEventListener('click', () => saveAdminAction('cancel', selected.id, null, booking.id));
    row.append(times, cancel);
    list.append(row);
  }
}

function adminSelection() {
  return spaces.find(space => space.id === selectedId);
}

function syncAdminControls() {
  const selected = adminSelection();
  const disabled = adminBusy || loading || !selected || !scheduleDate;
  const available = selected && spaceStatus(selected) === 'available';
  occupyButton.disabled = disabled || !available;
  syncTimeInputs(disabled);
  reserveButton.disabled = disabled || !reservationRangeValid();
  freeButton.disabled = disabled || available;
  renderReservations(selected, disabled);
  carSource.draggable = !adminBusy && !loading && spaces.length > 0;
  carSource.setAttribute('aria-disabled', String(!carSource.draggable));
  refreshButton.disabled = adminBusy || loading;
  lot.querySelectorAll('[data-id]').forEach(button => {
    const space = spaces.find(item => item.id === Number(button.dataset.id));
    button.draggable = !adminBusy && !loading && Boolean(space && space.occupied);
  });
  const until = document.querySelector('#reservation-until');
  until.hidden = !selected || !selected.reserved;
  until.textContent = selected && selected.reserved
    ? `Reserved until ${parkingClock(new Date(selected.reserved_until)).date !== scheduleDate
      ? '24:00' : parkingClock(new Date(selected.reserved_until)).time}` : '';
}

function actionMessage(text, error = false) {
  adminMessage.hidden = !text;
  adminMessage.textContent = text;
  adminMessage.classList.toggle('is-error', error);
}

async function saveAdminAction(action, id, sourceId = null, bookingId = null) {
  if (adminBusy || loading || !Number.isInteger(id)) return;
  if (action === 'reserve' && !reservationRangeValid()) {
    actionMessage('Choose a start and end time between now and midnight today.', true);
    return;
  }
  adminBusy = true;
  document.body.classList.add('is-saving');
  syncAdminControls();
  actionMessage('Saving…');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const values = new URLSearchParams({ csrf_token: document.body.dataset.csrf, action });
    if (action === 'reserve') {
      values.set('date', scheduleDate);
      values.set('start_time', startInput.value);
      values.set('end_time', midnightInput.checked ? '24:00' : endInput.value);
    }
    if (sourceId !== null) values.set('source_id', String(sourceId));
    if (bookingId !== null) values.set('booking_id', bookingId);
    const response = await fetch(`/api/admin/parking-spaces/${id}`, {
      method: 'POST', credentials: 'same-origin', body: values, signal: controller.signal
    });
    const payload = await response.json();
    if (!response.ok || payload.ok !== true) {
      const error = new Error('Action failed');
      error.userMessage = typeof payload.error === 'string' ? payload.error.slice(0, 220) : 'The change could not be saved.';
      throw error;
    }
    selectedId = id;
    const refreshed = await loadSpaces();
    actionMessage(refreshed ? 'Saved to Supabase.' : 'Change saved. Refresh to load the latest map.', !refreshed);
  } catch (error) {
    await loadSpaces();
    actionMessage(error.userMessage || 'Connection interrupted. Refresh to check whether the change was saved.', true);
  } finally {
    clearTimeout(timeout);
    adminBusy = false;
    document.body.classList.remove('is-saving');
    syncAdminControls();
  }
}

function addSelectedCar() {
  const space = adminSelection();
  if (space && spaceStatus(space) === 'available') saveAdminAction('occupy', space.id);
}

occupyButton.addEventListener('click', addSelectedCar);
reserveButton.addEventListener('click', () => saveAdminAction('reserve', selectedId));
freeButton.addEventListener('click', () => saveAdminAction('free', selectedId));
carSource.addEventListener('click', addSelectedCar);
carSource.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    addSelectedCar();
  }
});

carSource.addEventListener('dragstart', event => {
  if (!carSource.draggable) { event.preventDefault(); return; }
  draggedCar = { kind: 'new' };
  event.dataTransfer.setData('text/plain', 'parking-car');
  event.dataTransfer.effectAllowed = 'copy';
  document.body.classList.add('is-dragging');
});

lot.addEventListener('dragstart', event => {
  const button = event.target.closest('[data-id]');
  if (!button || !button.draggable) { event.preventDefault(); return; }
  draggedCar = { kind: 'parked', id: Number(button.dataset.id) };
  event.dataTransfer.setData('text/plain', 'parking-car');
  event.dataTransfer.effectAllowed = 'move';
  document.body.classList.add('is-dragging');
});

lot.addEventListener('dragover', event => {
  const button = event.target.closest('[data-id]');
  if (!draggedCar || adminBusy || loading || !button || button.dataset.status !== 'available') return;
  event.preventDefault();
  event.dataTransfer.dropEffect = draggedCar.kind === 'new' ? 'copy' : 'move';
  lot.querySelectorAll('.drop-target').forEach(item => item.classList.remove('drop-target'));
  button.classList.add('drop-target');
});

lot.addEventListener('dragleave', event => {
  const button = event.target.closest('[data-id]');
  if (button && !button.contains(event.relatedTarget)) button.classList.remove('drop-target');
});

lot.addEventListener('drop', event => {
  event.preventDefault();
  const button = event.target.closest('[data-id]');
  const car = draggedCar;
  clearDrag();
  if (!car || !button || button.dataset.status !== 'available') return;
  saveAdminAction(car.kind === 'new' ? 'occupy' : 'move', Number(button.dataset.id), car.id || null);
});

carExit.addEventListener('dragover', event => {
  if (!draggedCar || draggedCar.kind !== 'parked' || adminBusy || loading) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  carExit.classList.add('drop-target');
});
carExit.addEventListener('dragleave', () => carExit.classList.remove('drop-target'));
carExit.addEventListener('drop', event => {
  event.preventDefault();
  const car = draggedCar;
  clearDrag();
  if (car && car.kind === 'parked') saveAdminAction('free', car.id);
});

function clearDrag() {
  draggedCar = null;
  document.body.classList.remove('is-dragging');
  document.querySelectorAll('.drop-target').forEach(item => item.classList.remove('drop-target'));
}

document.addEventListener('dragend', clearDrag);
document.addEventListener('parking:selection', syncAdminControls);
document.addEventListener('parking:loading', syncAdminControls);
document.addEventListener('parking:ready', syncAdminControls);
document.addEventListener('parking:data', event => {
  if (!event.detail.schedule_date || !event.detail.server_time) return;
  scheduleDate = event.detail.schedule_date;
  serverOffset = Date.parse(event.detail.server_time) - Date.now();
  document.querySelector('#reservation-date').textContent = `Today · ${new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest', day: 'numeric', month: 'long', year: 'numeric'
  }).format(new Date(event.detail.server_time))} · Romanian time`;
  syncAdminControls();
});
[startInput, endInput, midnightInput].forEach(input => input.addEventListener('input', syncAdminControls));
setInterval(() => {
  if (scheduleDate && parkingClock().date !== scheduleDate && !adminBusy && !loading) loadSpaces();
  else syncAdminControls();
}, 30000);
syncAdminControls();
