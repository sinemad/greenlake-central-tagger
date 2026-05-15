const output = document.getElementById('output');
const syncButton = document.getElementById('sync-button');
const fetchButton = document.getElementById('fetch-button');
const gatewaySelect = document.getElementById('ac-gateway-select');
const gatewayUrlInput = document.getElementById('ac-base-url');

gatewaySelect.addEventListener('change', () => {
  if (gatewaySelect.value) gatewayUrlInput.value = gatewaySelect.value;
});

gatewayUrlInput.addEventListener('input', () => {
  const matched = Array.from(gatewaySelect.options).find(o => o.value === gatewayUrlInput.value.trim());
  gatewaySelect.value = matched ? matched.value : '';
});
const tagPanel = document.getElementById('tag-panel');
const tagGrid = document.getElementById('tag-grid');
const tagCount = document.getElementById('tag-count');
const selectAllButton = document.getElementById('select-all-button');
const deselectAllButton = document.getElementById('deselect-all-button');

const glCustomUrlToggle = document.getElementById('gl-custom-url-toggle');
const glApiUrlDisplay = document.getElementById('gl-api-url-display');
const glApiUrlInput = document.getElementById('gl-api-url');

glCustomUrlToggle.addEventListener('change', () => {
  if (glCustomUrlToggle.checked) {
    glApiUrlInput.value = glApiUrlDisplay.href;
    glApiUrlInput.style.display = '';
    glApiUrlDisplay.style.display = 'none';
  } else {
    glApiUrlInput.style.display = 'none';
    glApiUrlDisplay.style.display = '';
  }
});

function log(message) {
  output.textContent += `\n${message}`;
  output.scrollTop = output.scrollHeight;
}

function clearLog() {
  output.textContent = '';
}

function getArubaConfig() {
  return {
    base_url: document.getElementById('ac-base-url').value.trim(),
    access_token: document.getElementById('ac-access-token').value.trim(),
  };
}

function getFormData() {
  const selectedSites = getSelectedSites();
  return {
    aruba: getArubaConfig(),
    greenlake: {
      api_url: glCustomUrlToggle.checked ? glApiUrlInput.value.trim() : undefined,
      client_id: document.getElementById('gl-client-id').value.trim(),
      client_secret: document.getElementById('gl-client-secret').value,
      tag_key: document.getElementById('gl-tag-key').value.trim() || 'ArubaCentralSite',
    },
    selected_sites: selectedSites.length > 0 ? selectedSites : undefined,
  };
}

function getSelectedSites() {
  return Array.from(tagGrid.querySelectorAll('input[type="checkbox"]:checked'))
    .map(cb => cb.value);
}

function updateTagCount() {
  const total = tagGrid.querySelectorAll('input[type="checkbox"]').length;
  const selected = tagGrid.querySelectorAll('input[type="checkbox"]:checked').length;
  tagCount.textContent = `${selected} of ${total} sites selected`;
}

function buildTagGrid(sites) {
  tagGrid.innerHTML = '';
  for (const site of sites) {
    const id = `tag-${CSS.escape(site)}`;
    const item = document.createElement('div');
    item.className = 'tag-item';

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = id;
    cb.value = site;
    cb.checked = true;
    cb.addEventListener('change', updateTagCount);

    const label = document.createElement('label');
    label.htmlFor = id;
    label.textContent = site;

    item.appendChild(cb);
    item.appendChild(label);
    tagGrid.appendChild(item);
  }
  updateTagCount();
}

async function fetchSites() {
  const aruba = getArubaConfig();
  if (!aruba.base_url || !aruba.access_token) {
    alert('Please fill in the Aruba Central API Gateway URL and Access Token.');
    return;
  }

  fetchButton.disabled = true;
  fetchButton.textContent = 'Fetching…';
  tagPanel.style.display = 'none';

  try {
    const response = await fetch('/api/sites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aruba }),
    });
    const result = await response.json();
    if (!response.ok) {
      alert(`Failed to fetch sites: ${result.detail || JSON.stringify(result)}`);
      return;
    }
    buildTagGrid(result.sites);
    tagPanel.style.display = '';
  } catch (error) {
    alert(`Unexpected error fetching sites: ${error}`);
  } finally {
    fetchButton.disabled = false;
    fetchButton.textContent = 'Fetch Sites';
  }
}

selectAllButton.addEventListener('click', () => {
  tagGrid.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
  updateTagCount();
});

deselectAllButton.addEventListener('click', () => {
  tagGrid.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
  updateTagCount();
});

fetchButton.addEventListener('click', fetchSites);

async function syncTags() {
  const payload = getFormData();
  clearLog();
  log('Starting sync...');
  syncButton.disabled = true;

  try {
    const response = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    let result;
    const rawText = await response.text();
    try {
      result = JSON.parse(rawText);
    } catch {
      log(`Error (${response.status}): ${rawText.slice(0, 500) || '(empty response)'}`);
      return;
    }

    if (!response.ok) {
      log(`Error: ${result.detail || JSON.stringify(result)}`);
      if (result.traceback) log(result.traceback);
      return;
    }

    log('Sync complete.');
    log(`Sites synced: ${result.site_count}`);
    if (result.sites?.length) {
      result.sites.forEach(s => log(`  • ${s}`));
    }
    log(`Access points processed: ${result.ap_count}`);
    log(`Matched to GreenLake devices: ${result.matched?.length ?? 0}`);
    if (result.unmatched?.length) {
      log(`Unmatched APs (no GreenLake device found): ${result.unmatched.length}`);
      result.unmatched.forEach(ap => log(`  • ${ap.ap_name} (${ap.serial}) — ${ap.reason}`));
    }
    if (result.patch_results?.length) {
      const totalPatched = result.patch_results.reduce((sum, r) => sum + (r.device_count || 0), 0);
      log(`Devices patched with tags: ${totalPatched}`);
    }
    log('Full response:');
    log(JSON.stringify(result, null, 2));
  } catch (error) {
    log(`Unexpected error: ${error}`);
  } finally {
    syncButton.disabled = false;
  }
}

syncButton.addEventListener('click', syncTags);
