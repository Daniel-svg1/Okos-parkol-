const bookingButton = document.querySelector('#open-booking');
const dialog = document.querySelector('#working-dialog');
const lot = document.querySelector('#parking-lot');
const refreshButton = document.querySelector('#refresh-data');
const message = document.querySelector('#parking-message');
const messageText = document.querySelector('#parking-message-text');
const signInLink = document.querySelector('#parking-sign-in');
const sourceBadge = document.querySelector('#data-source');
const priceFormat = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 });
let spaces = [];
let selectedId = null;
let loading = false;
const isAdmin = document.body.dataset.admin === 'true';

function spaceStatus(space) {
  return space.occupied ? 'occupied' : space.reserved ? 'reserved' : 'available';
}

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

function element(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function lineIcon(path) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.7');
  svg.setAttribute('aria-hidden', 'true');
  const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  shape.setAttribute('d', path);
  svg.append(shape);
  return svg;
}

function selectSpace(id) {
  const space = spaces.find(item => item.id === id);
  selectedId = space ? space.id : null;
  lot.querySelectorAll('[data-id]').forEach(button => {
    const selected = Number(button.dataset.id) === selectedId;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  document.querySelector('#selected-label').textContent = space ? space.label : '—';
  document.querySelector('#map-selection').textContent = space ? `${space.label} selected` : 'No space selected';
  const badge = document.querySelector('#selected-status');
  const status = space ? spaceStatus(space) : '';
  badge.className = `status-badge ${status}`;
  badge.querySelector('span').textContent = space ? status[0].toUpperCase() + status.slice(1) : 'No selection';
  document.querySelector('#selected-description').textContent = space
    ? (status === 'occupied' ? 'Currently occupied' : status === 'reserved' ? 'Currently reserved' : 'Available to book') : 'Select a parking space';
  document.querySelector('#selected-rate').textContent = space ? priceFormat.format(space.hourly_rate) : '—';
  if (bookingButton) bookingButton.disabled = !space;
  document.querySelector('#selection-note').textContent = isAdmin
    ? 'Changes are saved to the live parking database. Make available also cancels the current reservation.'
    : space
    ? (space.occupied ? 'Currently occupied. You can choose a later time on the Booking page.' : 'Choose your start and end time on the next page.')
    : 'Choose a space to view its details.';
  document.dispatchEvent(new CustomEvent('parking:selection', { detail: space || null }));
}

function renderSpaces(rows) {
  const previousId = selectedId;
  spaces = rows;
  const content = document.createDocumentFragment();
  const strip = element('div', 'green-strip');
  strip.setAttribute('aria-hidden', 'true');
  content.append(strip);
  const levels = [...new Set(rows.map(space => space.map_y))];
  levels.forEach((level, index) => {
    if (index > 0) {
      const driveway = element('div', 'driveway');
      driveway.setAttribute('aria-hidden', 'true');
      driveway.append(element('span', '', 'IN'), element('span', 'lane-line'),
        lineIcon('M4 12h16m-6-6 6 6-6 6'), element('span', 'lane-line'), element('span', '', 'OUT'));
      content.append(driveway);
    }
    const row = element('div', 'parking-row');
    rows.filter(space => space.map_y === level).forEach(space => {
      const status = spaceStatus(space);
      const button = element('button', `parking-space ${status}`);
      button.type = 'button';
      button.dataset.id = space.id;
      button.dataset.space = space.label;
      button.dataset.status = status;
      button.style.gridColumn = String(space.map_x);
      button.setAttribute('aria-label', `${space.label}, ${status}`);
      button.setAttribute('aria-pressed', 'false');
      button.append(element('span', 'space-label', space.label));
      const symbol = space.occupied ? element('span', 'car-emoji', '🚗')
        : space.reserved ? lineIcon('M3 5h18v16H3zM3 10h18M8 3v4M16 3v4M8 14h2M14 14h2M8 18h2')
        : lineIcon('m5 12 4 4L19 6');
      symbol.setAttribute('aria-hidden', 'true');
      button.append(symbol);
      button.addEventListener('click', () => selectSpace(space.id));
      row.append(button);
    });
    content.append(row);
  });
  lot.replaceChildren(rows.length ? content : document.createDocumentFragment());
  document.querySelector('#available-count').textContent = rows.filter(space => spaceStatus(space) === 'available').length;
  document.querySelector('#occupied-count').textContent = rows.filter(space => space.occupied).length;
  document.querySelector('#reserved-count').textContent = rows.filter(space => spaceStatus(space) === 'reserved').length;
  document.querySelector('#space-count').textContent = `${rows.length} ${rows.length === 1 ? 'space' : 'spaces'}`;
  const selected = rows.find(space => space.id === previousId) || rows.find(space => space.label === 'A3')
    || rows.find(space => spaceStatus(space) === 'available') || rows[0];
  selectSpace(selected ? selected.id : null);
}

function validPayload(payload) {
  if (!payload || !Array.isArray(payload.spaces) || !['local_demo', 'supabase'].includes(payload.source)
      || typeof payload.fetched_at !== 'string' || Number.isNaN(Date.parse(payload.fetched_at))) return false;
  const ids = new Set(), labels = new Set(), coordinates = new Set();
  return payload.spaces.every(space => {
    if (!space || !Number.isInteger(space.id) || space.id < 1
        || typeof space.label !== 'string' || !space.label.trim() || space.label.length > 10
        || typeof space.occupied !== 'boolean' || typeof space.hourly_rate !== 'number'
        || !Number.isFinite(space.hourly_rate) || space.hourly_rate < 0 || space.hourly_rate > 999999.99
        || !Number.isInteger(space.map_x) || space.map_x < 1 || space.map_x > 6
        || !Number.isInteger(space.map_y) || space.map_y < 1 || space.map_y > 99
        || (space.reserved !== undefined && typeof space.reserved !== 'boolean')
        || (space.reserved && (typeof space.reserved_until !== 'string'
          || Number.isNaN(Date.parse(space.reserved_until))))) return false;
    const coordinate = `${space.map_x},${space.map_y}`;
    if (ids.has(space.id) || labels.has(space.label) || coordinates.has(coordinate)) return false;
    ids.add(space.id);
    labels.add(space.label);
    coordinates.add(coordinate);
    return true;
  });
}

async function loadSpaces() {
  if (loading) return false;
  let success = false;
  loading = true;
  refreshButton.disabled = true;
  lot.setAttribute('aria-busy', 'true');
  document.dispatchEvent(new Event('parking:loading'));
  message.hidden = false;
  message.classList.remove('is-error');
  messageText.textContent = 'Loading parking spaces…';
  signInLink.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(document.body.dataset.parkingUrl, {
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal
    });
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error('Parking request failed');
      error.userMessage = typeof payload.error === 'string' ? payload.error.slice(0, 200) : '';
      error.signIn = response.status === 401;
      throw error;
    }
    if (!validPayload(payload)) throw new Error('Invalid parking response');
    document.dispatchEvent(new CustomEvent('parking:data', { detail: payload }));
    renderSpaces([...payload.spaces].sort((a, b) => a.map_y - b.map_y || a.map_x - b.map_x));
    message.hidden = payload.spaces.length > 0;
    messageText.textContent = 'No parking spaces are available to display yet.';
    sourceBadge.querySelector('span').textContent = payload.source === 'local_demo' ? 'Local demo' : 'Parking data';
    sourceBadge.title = payload.source === 'local_demo' ? 'Sample data from a local database' : 'Parking data from the database';
    const fetched = new Date(payload.fetched_at);
    document.querySelector('[data-display-date]').textContent = fetched.toLocaleDateString('en-GB', {
      day: 'numeric', month: 'long', year: 'numeric', ...(isAdmin ? { timeZone: 'Europe/Bucharest' } : {})
    });
    document.querySelector('#last-refreshed').textContent = `Updated ${fetched.toLocaleTimeString('en-GB', isAdmin ? { timeZone: 'Europe/Bucharest' } : {})}`;
    success = true;
  } catch (error) {
    spaces = [];
    lot.replaceChildren();
    selectSpace(null);
    ['available-count', 'occupied-count', 'reserved-count'].forEach(id => {
      document.getElementById(id).textContent = '—';
    });
    document.querySelector('#space-count').textContent = '— spaces';
    document.querySelector('#last-refreshed').textContent = 'Not refreshed';
    sourceBadge.querySelector('span').textContent = 'Unavailable';
    message.hidden = false;
    message.classList.add('is-error');
    messageText.textContent = error.name === 'AbortError' ? 'Parking data took too long to load. Please try again.'
      : error.userMessage || 'Parking data could not be loaded. Please try again.';
    signInLink.hidden = !error.signIn;
  } finally {
    clearTimeout(timeout);
    loading = false;
    refreshButton.disabled = false;
    lot.setAttribute('aria-busy', 'false');
    document.dispatchEvent(new Event('parking:ready'));
  }
  return success;
}

document.querySelector('[data-display-date]').textContent = new Date().toLocaleDateString('en-GB', {
  day: 'numeric', month: 'long', year: 'numeric'
});
refreshButton.addEventListener('click', loadSpaces);
if (bookingButton) bookingButton.addEventListener('click', () => {
  if (selectedId !== null) window.location.href = `/booking?space=${selectedId}`;
});
loadSpaces();
