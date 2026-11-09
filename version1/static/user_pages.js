const userPage = document.body.dataset.userPage;
const message = document.querySelector('#user-message');
const messageText = document.querySelector('#user-message-text');
const signInLink = document.querySelector('#user-sign-in');
const refresh = document.querySelector('#user-refresh');
const money = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 });
const clockFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Bucharest', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
});
let overview = null;
let loading = false;
let saving = false;
let serverOffset = 0;
let inputDay = null;
let nextBoundary = Infinity;
let lastMinute = '';

function node(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function clock(value = Date.now() + serverOffset) {
  const parts = Object.fromEntries(clockFormat.formatToParts(new Date(value)).map(part => [part.type, part.value]));
  return { date: parts.year + '-' + parts.month + '-' + parts.day, time: parts.hour + ':' + parts.minute };
}

function dayLabel(value) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest', day: 'numeric', month: 'long', year: 'numeric'
  }).format(new Date(value));
}

function intervalLabel(booking) {
  const start = clock(booking.start_time), end = clock(booking.end_time);
  return start.time + ' – ' + (end.date !== start.date ? '24:00' : end.time);
}

function notify(text, error = false, signIn = false) {
  message.hidden = !text;
  message.classList.toggle('is-error', error);
  messageText.textContent = text;
  signInLink.hidden = !signIn;
}

function minutes(value) {
  const [hours, mins] = value.split(':').map(Number);
  return hours * 60 + mins;
}

function duration(value) {
  const mins = Math.max(0, Math.round(value));
  const hours = Math.floor(mins / 60), remainder = mins % 60;
  return [hours ? hours + (hours === 1 ? ' hour' : ' hours') : '',
    remainder ? remainder + (remainder === 1 ? ' minute' : ' minutes') : ''].filter(Boolean).join(' ') || '0 minutes';
}

function validOverview(data) {
  return data && Array.isArray(data.spaces) && Array.isArray(data.bookings)
    && data.timezone === 'Europe/Bucharest' && /^\d{4}-\d{2}-\d{2}$/.test(data.schedule_date)
    && Number.isFinite(Date.parse(data.server_time))
    && data.spaces.every(space => Number.isInteger(space.id) && typeof space.label === 'string'
      && typeof space.occupied === 'boolean' && Number.isFinite(space.hourly_rate) && Array.isArray(space.intervals))
    && data.bookings.every(booking => typeof booking.id === 'string'
      && ['confirmed', 'completed', 'cancelled'].includes(booking.status)
      && Number.isFinite(Date.parse(booking.start_time)) && Number.isFinite(Date.parse(booking.end_time)));
}

async function loadOverview(quiet = false) {
  if (loading) return false;
  loading = true;
  refresh.disabled = true;
  if (!quiet) notify('Loading your parking information…');
  updatePage();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  let success = false;
  try {
    const response = await fetch('/api/bookings', {
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error('Loading failed');
      error.userMessage = data.error;
      error.signIn = response.status === 401;
      throw error;
    }
    if (!validOverview(data)) throw new Error('Invalid parking information');
    overview = data;
    serverOffset = Date.parse(data.server_time) - Date.now();
    document.querySelector('#data-source span').textContent = 'Parking data';
    notify('');
    success = true;
  } catch (error) {
    overview = null;
    document.querySelector('#data-source span').textContent = 'Unavailable';
    notify(error.userMessage || 'Your parking information could not be loaded. Please refresh.', true, error.signIn);
  } finally {
    clearTimeout(timeout);
    loading = false;
    refresh.disabled = saving;
    updatePage();
  }
  return success;
}

function timeRange() {
  const start = document.querySelector('#booking-start').value;
  const end = document.querySelector('#booking-midnight').checked ? '24:00' : document.querySelector('#booking-end').value;
  const current = clock();
  return { start, end, valid: Boolean(overview && start && end && overview.schedule_date === current.date
    && start >= current.time && end > start && end <= '24:00') };
}

function spaceAvailable(space, range) {
  if (!space || !range.valid) return false;
  if (space.occupied && range.start === clock().time) return false;
  const start = minutes(range.start), end = minutes(range.end);
  return !space.intervals.some(interval => {
    const from = clock(interval.start_time), to = clock(interval.end_time);
    const fromMinute = from.date < overview.schedule_date ? 0 : minutes(from.time);
    const toMinute = to.date > overview.schedule_date ? 1440 : minutes(to.time);
    return start < toMinute && end > fromMinute;
  });
}

function renderBooking() {
  const select = document.querySelector('#booking-space');
  const start = document.querySelector('#booking-start');
  const end = document.querySelector('#booking-end');
  const midnight = document.querySelector('#booking-midnight');
  const confirm = document.querySelector('#confirm-booking');
  const disabled = loading || saving || !overview;
  [select, start, midnight].forEach(field => { field.disabled = disabled; });
  end.disabled = disabled || midnight.checked;
  refresh.disabled = loading || saving;
  confirm.disabled = true;
  if (!overview) {
    const placeholder = node('option', '', 'Choose a space');
    placeholder.value = '';
    select.replaceChildren(placeholder);
    document.querySelector('#booking-label').textContent = '—';
    return;
  }
  if (inputDay !== overview.schedule_date) {
    inputDay = overview.schedule_date;
    start.value = clock().time;
    const ending = Math.min(1440, minutes(start.value) + 60);
    midnight.checked = ending === 1440;
    end.value = ending === 1440 ? '23:59'
      : String(Math.floor(ending / 60)).padStart(2, '0') + ':' + String(ending % 60).padStart(2, '0');
  }
  end.disabled = disabled || midnight.checked;
  end.hidden = midnight.checked;
  document.querySelector('#booking-midnight-value').hidden = !midnight.checked;
  start.min = clock().time;
  start.max = '23:59';
  end.min = start.value;
  end.max = '23:59';
  document.querySelector('#booking-day').textContent = 'Today · ' + dayLabel(overview.server_time) + ' · Romanian time';
  const range = timeRange();
  const previous = Number(select.value || document.body.dataset.selectedSpace);
  select.replaceChildren();
  for (const space of overview.spaces) {
    const option = node('option', '', space.label + ' · ' + (spaceAvailable(space, range) ? 'Available' : 'Unavailable for this time'));
    option.value = String(space.id);
    select.append(option);
  }
  const chosen = overview.spaces.find(space => space.id === previous)
    || overview.spaces.find(space => spaceAvailable(space, range)) || overview.spaces[0];
  select.value = chosen ? String(chosen.id) : '';
  const available = spaceAvailable(chosen, range);
  document.querySelector('#booking-label').textContent = chosen ? chosen.label : '—';
  const badge = document.querySelector('#booking-availability');
  badge.className = 'status-badge ' + (available ? 'available' : 'occupied');
  badge.querySelector('span').textContent = available ? 'Available' : 'Unavailable';
  document.querySelector('#space-help').textContent = !range.valid ? 'Choose a valid time between now and midnight.'
    : available ? 'Available for your selected time.' : 'Choose another time or parking space.';
  document.querySelector('#summary-date').textContent = dayLabel(overview.server_time);
  document.querySelector('#summary-time').textContent = range.start && range.end ? range.start + ' – ' + range.end : '—';
  document.querySelector('#summary-rate').textContent = chosen ? money.format(chosen.hourly_rate) + ' RON / hour' : '—';
  const mins = range.valid ? minutes(range.end) - minutes(range.start) : null;
  document.querySelector('#summary-duration').textContent = mins === null ? '—' : duration(mins);
  document.querySelector('#summary-total').textContent = chosen && mins !== null
    ? money.format(chosen.hourly_rate * mins / 60) + ' RON' : '—';
  confirm.disabled = disabled || !available;
  confirm.firstChild.textContent = saving ? 'Saving booking… ' : 'Confirm booking ';
  const schedule = document.querySelector('#space-schedule');
  schedule.replaceChildren();
  if (chosen && chosen.intervals.length) {
    schedule.append(node('p', '', 'Already reserved today · ' + chosen.label));
    const intervals = node('div', 'interval-chips');
    for (const interval of chosen.intervals) intervals.append(node('span', '', intervalLabel(interval)));
    schedule.append(intervals);
  }
}

function emptyCard(title, text) {
  const card = node('div', 'card empty-parking');
  card.append(node('span', 'empty-clock', '◷'), node('h3', '', title), node('p', '', text));
  return card;
}

function actionButton(text, action, booking, primary = false, disabled = false) {
  const button = node('button', primary ? 'primary-button user-action' : 'secondary-user-button', text);
  button.type = 'button';
  button.disabled = saving || loading || disabled;
  button.dataset.bookingId = booking.id;
  button.dataset.action = action;
  button.addEventListener('click', () => changeBooking(booking, action));
  return button;
}

function timer(value, kind, className = '') {
  const span = node('strong', className);
  span.dataset.timerAt = value;
  span.dataset.timerKind = kind;
  return span;
}

function renderActive() {
  const current = document.querySelector('#current-parking');
  const reservations = document.querySelector('#user-reservations');
  const history = document.querySelector('#user-history');
  current.replaceChildren(); reservations.replaceChildren(); history.replaceChildren();
  if (!overview) {
    ['user-active-count', 'user-upcoming-count', 'user-ready-count'].forEach(id => { document.getElementById(id).textContent = '—'; });
    return;
  }
  const now = Date.now() + serverOffset;
  const running = overview.bookings.filter(b => b.status === 'confirmed' && b.actual_start && !b.actual_end);
  const waiting = overview.bookings.filter(b => b.status === 'confirmed' && !b.actual_start && Date.parse(b.end_time) > now)
    .sort((a, b) => Date.parse(a.start_time) - Date.parse(b.start_time));
  const closed = overview.bookings.filter(b => !running.includes(b) && !waiting.includes(b)).slice(0, 20);
  document.querySelector('#user-active-count').textContent = running.length;
  document.querySelector('#user-upcoming-count').textContent = waiting.filter(b => Date.parse(b.start_time) > now).length;
  document.querySelector('#user-ready-count').textContent = waiting.filter(b => Date.parse(b.start_time) <= now).length;
  for (const booking of running) {
    const card = node('article', 'card live-parking-card');
    card.dataset.bookingId = booking.id;
    const header = node('div', 'live-parking-heading');
    const text = node('div', '');
    text.append(node('span', 'status-badge available', 'Parking in progress'), node('h3', '', 'Space ' + booking.label));
    header.append(text, node('span', 'live-car', '🚗'));
    const time = node('div', 'live-timer');
    time.append(node('span', 'timer-caption', 'Time remaining'), timer(booking.end_time, 'remaining', 'session-countdown'));
    const details = node('div', 'live-details');
    const elapsed = node('p', ''); elapsed.append(node('span', '', 'Elapsed'), timer(booking.actual_start, 'elapsed'));
    const until = node('p', ''); until.append(node('span', '', 'Reserved time'), node('strong', '', intervalLabel(booking)));
    const rate = node('p', ''); rate.append(node('span', '', 'Hourly rate'), node('strong', '', money.format(booking.hourly_rate) + ' RON / hour'));
    details.append(elapsed, until, rate);
    card.append(header, time, details, actionButton('Finish parking', 'finish', booking, true));
    current.append(card);
  }
  if (!running.length) current.append(emptyCard('No active parking', 'When you arrive, start a reservation below to begin your parking session.'));
  for (const booking of waiting) {
    const ready = Date.parse(booking.start_time) <= now;
    const card = node('article', 'card reservation-ticket');
    card.dataset.bookingId = booking.id;
    if (new URLSearchParams(location.search).get('booking') === booking.id) card.classList.add('is-highlighted');
    const info = node('div', 'ticket-info');
    info.append(node('span', 'status-badge ' + (ready ? 'available' : 'reserved'), ready ? 'Ready to start' : 'Reserved'),
      node('h3', '', 'Space ' + booking.label), node('p', '', dayLabel(booking.start_time) + ' · ' + intervalLabel(booking)));
    const estimate = node('div', 'ticket-estimate');
    estimate.append(node('span', '', 'Estimated total'), node('strong', '', money.format(booking.estimated_total) + ' RON'));
    const actions = node('div', 'ticket-actions');
    if (!ready) {
      const begins = node('p', 'ticket-begins', 'Starts in ');
      begins.append(timer(booking.start_time, 'starts'));
      actions.append(begins);
    }
    actions.append(actionButton('Start parking', 'start', booking, true, !ready || running.length > 0),
      actionButton('Cancel booking', 'cancel', booking));
    card.append(info, estimate, actions);
    reservations.append(card);
  }
  if (!waiting.length) reservations.append(emptyCard('No upcoming reservations', 'Choose a space on the Booking page to make a new reservation.'));
  for (const booking of closed) {
    const row = node('div', 'history-row');
    const info = node('div', '');
    info.append(node('strong', '', 'Space ' + booking.label), node('span', '', dayLabel(booking.start_time) + ' · ' + intervalLabel(booking)));
    const status = booking.status === 'confirmed' ? 'Expired' : booking.status === 'completed' ? 'Completed' : 'Cancelled';
    row.append(info, node('span', 'small-badge ' + (status === 'Completed' ? 'available' : ''), status));
    history.append(row);
  }
  if (!closed.length) history.append(node('p', 'history-empty', 'Your completed and cancelled bookings will appear here.'));
  nextBoundary = Math.min(Infinity, ...overview.bookings.flatMap(b => [Date.parse(b.start_time), Date.parse(b.end_time)]).filter(value => value > now));
  updateTimers();
}

function updateTimers() {
  const now = Date.now() + serverOffset;
  document.querySelectorAll('[data-timer-at]').forEach(element => {
    let difference = element.dataset.timerKind === 'elapsed' ? now - Date.parse(element.dataset.timerAt)
      : Date.parse(element.dataset.timerAt) - now;
    const overdue = element.dataset.timerKind === 'remaining' && difference < 0;
    difference = Math.max(0, Math.abs(difference));
    const seconds = Math.floor(difference / 1000);
    element.textContent = String(Math.floor(seconds / 3600)).padStart(2, '0') + ':'
      + String(Math.floor(seconds / 60) % 60).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    element.classList.toggle('is-overdue', overdue);
    if (element.classList.contains('session-countdown')) element.previousElementSibling.textContent = overdue ? 'Reservation ended · overtime' : 'Time remaining';
  });
}

function updatePage() {
  refresh.disabled = loading || saving;
  if (userPage === 'booking') renderBooking();
  else renderActive();
}

async function post(url, values) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(url, { method: 'POST', credentials: 'same-origin',
      body: new URLSearchParams({ csrf_token: document.body.dataset.csrf, ...values }), signal: controller.signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok !== true) {
      const error = new Error('Parking action failed');
      error.userMessage = payload.error || 'The action could not be completed. Refresh the page and try again.';
      error.signIn = response.status === 401;
      throw error;
    }
    return payload;
  } finally { clearTimeout(timeout); }
}

async function createBooking(event) {
  event.preventDefault();
  if (saving || loading || !overview) return;
  const range = timeRange();
  const id = Number(document.querySelector('#booking-space').value);
  if (!spaceAvailable(overview.spaces.find(space => space.id === id), range)) return;
  saving = true; updatePage(); notify('Saving your booking…');
  try {
    const result = await post('/api/bookings', { space_id: id, date: overview.schedule_date,
      start_time: range.start, end_time: range.end });
    location.href = '/active-parking?booking=' + encodeURIComponent(result.booking_id);
  } catch (error) {
    await loadOverview();
    notify(error.userMessage || 'Connection interrupted. Check Active parking before trying again.', true, error.signIn);
    saving = false; updatePage();
  }
}

async function changeBooking(booking, action) {
  if (saving || loading) return;
  saving = true; updatePage(); notify('Saving your parking information…');
  try {
    await post('/api/bookings/' + encodeURIComponent(booking.id) + '/' + action, {});
    const success = await loadOverview();
    if (success) notify(action === 'start' ? 'Parking started.' : action === 'finish' ? 'Parking finished. Your space is now available.' : 'Booking cancelled.');
  } catch (error) {
    await loadOverview();
    notify(error.userMessage || 'Connection interrupted. Refresh to check the latest state.', true, error.signIn);
  } finally { saving = false; updatePage(); }
}

refresh.addEventListener('click', () => loadOverview());
if (userPage === 'booking') {
  document.querySelector('#booking-form').addEventListener('submit', createBooking);
  ['booking-space', 'booking-start', 'booking-end', 'booking-midnight'].forEach(id => {
    document.getElementById(id).addEventListener('input', renderBooking);
  });
}
const dialog = document.querySelector('#working-dialog');
document.querySelectorAll('[data-placeholder]').forEach(button => button.addEventListener('click', () => dialog.showModal()));
document.querySelector('.close-dialog').addEventListener('click', () => dialog.close());
setInterval(() => {
  if (!overview) return;
  if (userPage === 'active') {
    updateTimers();
    if (Date.now() + serverOffset >= nextBoundary && !saving && !loading) loadOverview(true);
  } else if (clock().time !== lastMinute) {
    lastMinute = clock().time;
    if (clock().date !== overview.schedule_date && !saving && !loading) loadOverview();
    else renderBooking();
  }
}, 1000);
setInterval(() => {
  if (userPage === 'active' && !document.hidden && !saving && !loading) loadOverview(true);
}, 20000);
loadOverview();

