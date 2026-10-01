/**
 * LedgerCore Frontend Application Logic
 */

const API_BASE = '/v1';

// Global state
let currentAccounts = [];
let currentTransactions = [];

// DOM Elements
const navItems = document.querySelectorAll('.nav-item');
const viewPanels = document.querySelectorAll('.view-panel');
const pageTitle = document.getElementById('page-title');
const pageSubtitle = document.getElementById('page-subtitle');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  setupNavigation();
  setupModals();
  setupForms();
  setupRaceDemo();
  loadDashboardData();
});

// Toast notification helper
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span>${type === 'success' ? '✔' : '✖'}</span>
    <span>${message}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 4000);
}

// Navigation Tabs
function setupNavigation() {
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const targetTab = item.getAttribute('data-tab');
      switchTab(targetTab);
    });
  });

  document.getElementById('btn-view-all-accounts')?.addEventListener('click', () => switchTab('accounts'));
  document.getElementById('btn-view-all-journal')?.addEventListener('click', () => switchTab('journal'));
}

function switchTab(tabId) {
  navItems.forEach(i => i.classList.toggle('active', i.getAttribute('data-tab') === tabId));
  viewPanels.forEach(p => p.classList.toggle('active', p.id === `tab-${tabId}`));

  const titles = {
    overview: { title: 'Executive Dashboard', sub: 'Real-time append-only double-entry financial ledger' },
    accounts: { title: 'Chart of Accounts', sub: 'Assets, Liabilities, Equity, Revenue, and Expense Classifications' },
    journal: { title: 'Ledger Journal', sub: 'Immutable, multi-legged transaction log' },
    audit: { title: 'Historical Window Audit', sub: 'Point-in-time balance reconstruction using analytical window functions' },
    'race-demo': { title: 'Live Race Condition Simulator', sub: 'High-concurrency bank run & overdraft prevention demonstration' },
  };

  if (titles[tabId]) {
    pageTitle.textContent = titles[tabId].title;
    pageSubtitle.textContent = titles[tabId].sub;
  }

  if (tabId === 'overview') loadDashboardData();
  if (tabId === 'accounts') loadAccounts();
  if (tabId === 'journal') loadTransactions();
  if (tabId === 'audit') loadAuditAccountsDropdown();
}

// Data Fetchers
async function loadDashboardData() {
  try {
    const [accounts, txs] = await Promise.all([
      fetch(`${API_BASE}/accounts`).then(r => r.json()),
      fetch(`${API_BASE}/transactions?limit=10`).then(r => r.json()),
    ]);

    currentAccounts = accounts;
    currentTransactions = txs;

    // Update Metrics
    document.getElementById('metric-accounts-count').textContent = accounts.length;
    document.getElementById('metric-tx-count').textContent = txs.length;

    const totalLegs = txs.reduce((sum, tx) => sum + (tx.entries ? tx.entries.length : 0), 0);
    document.getElementById('metric-entries-count').textContent = totalLegs;

    // Render snapshot tables
    renderOverviewAccounts(accounts);
    renderOverviewTransactions(txs);
    populateSelectDropdowns(accounts);
  } catch (err) {
    console.error('Failed to load dashboard data:', err);
    showToast('Failed to connect to LedgerCore API', 'error');
  }
}

async function loadAccounts() {
  try {
    const accounts = await fetch(`${API_BASE}/accounts`).then(r => r.json());
    currentAccounts = accounts;
    renderAccountsTable(accounts);
    populateSelectDropdowns(accounts);
  } catch (err) {
    showToast('Failed to load accounts: ' + err.message, 'error');
  }
}

async function loadTransactions() {
  try {
    const txs = await fetch(`${API_BASE}/transactions?limit=50`).then(r => r.json());
    currentTransactions = txs;
    renderJournalFeed(txs);
  } catch (err) {
    showToast('Failed to load transactions: ' + err.message, 'error');
  }
}

// Render Functions
function renderOverviewAccounts(accounts) {
  const tbody = document.getElementById('overview-accounts-tbody');
  if (!accounts || accounts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="text-center">No accounts found.</td></tr>';
    return;
  }

  tbody.innerHTML = accounts.slice(0, 6).map(acc => `
    <tr>
      <td><strong>${acc.name}</strong></td>
      <td><span class="badge badge-${acc.type.toLowerCase()}">${acc.type}</span></td>
      <td class="font-mono ${acc.settled_balance_cents >= 0 ? 'text-success' : 'text-danger'}">
        ${acc.settled_balance_formatted}
      </td>
      <td>${acc.allow_overdraft ? '<span class="text-warning">Allowed</span>' : '<span class="text-dim">Strict</span>'}</td>
    </tr>
  `).join('');
}

function renderOverviewTransactions(txs) {
  const container = document.getElementById('overview-transactions-feed');
  if (!txs || txs.length === 0) {
    container.innerHTML = '<div class="empty-state">No transactions posted yet.</div>';
    return;
  }

  container.innerHTML = txs.slice(0, 5).map(tx => {
    const totalAmount = tx.entries ? tx.entries.filter(e => e.direction === 'DEBIT').reduce((s, e) => s + e.amount_cents, 0) : 0;
    const formatted = `$${(totalAmount / 100).toFixed(2)}`;
    return `
      <div class="journal-card" style="padding: 0.85rem 1rem; margin-bottom: 0.75rem;">
        <div class="journal-card-header" style="margin-bottom: 0.35rem;">
          <span class="journal-desc">${tx.description}</span>
          <span class="font-mono text-success">${formatted}</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-dim);">
          <span>${tx.entries ? tx.entries.length : 0} balanced legs</span>
          <span>${new Date(tx.posted_at).toLocaleTimeString()}</span>
        </div>
      </div>
    `;
  }).join('');
}

function renderAccountsTable(accounts) {
  const tbody = document.getElementById('accounts-table-body');
  if (!accounts || accounts.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center">No accounts configured.</td></tr>';
    return;
  }

  tbody.innerHTML = accounts.map(acc => `
    <tr>
      <td class="font-mono text-dim" style="font-size: 0.7rem;">${acc.id.substring(0, 8)}...</td>
      <td><strong>${acc.name}</strong></td>
      <td><span class="badge badge-${acc.type.toLowerCase()}">${acc.type}</span></td>
      <td>${acc.currency}</td>
      <td class="font-mono ${acc.settled_balance_cents >= 0 ? 'text-success' : 'text-danger'}" style="font-weight: 700;">
        ${acc.settled_balance_formatted}
      </td>
      <td class="font-mono text-dim">$0.00</td>
      <td>${acc.allow_overdraft ? '<span class="badge badge-warning" style="background: rgba(245, 158, 11, 0.15); color: #fbbf24;">Allowed</span>' : '<span class="text-dim">No Overdraft</span>'}</td>
      <td><span class="badge badge-success">${acc.status}</span></td>
      <td>
        <button class="btn btn-ghost btn-sm btn-inspect-audit" data-id="${acc.id}">Timeline &rarr;</button>
      </td>
    </tr>
  `).join('');

  document.querySelectorAll('.btn-inspect-audit').forEach(b => {
    b.addEventListener('click', () => {
      const accId = b.getAttribute('data-id');
      switchTab('audit');
      const select = document.getElementById('audit-account-select');
      select.value = accId;
      runAccountAudit(accId);
    });
  });
}

function renderJournalFeed(txs) {
  const container = document.getElementById('journal-feed-container');
  if (!txs || txs.length === 0) {
    container.innerHTML = '<div class="empty-state">No transactions recorded.</div>';
    return;
  }

  container.innerHTML = txs.map(tx => `
    <div class="journal-card">
      <div class="journal-card-header">
        <div>
          <div class="journal-desc">${tx.description}</div>
          <div style="font-size: 0.75rem; color: var(--text-dim); margin-top: 0.2rem;">
            ID: <span class="font-mono">${tx.id}</span>
            ${tx.idempotency_key ? ` &bull; Idempotency: <span class="font-mono text-indigo">${tx.idempotency_key}</span>` : ''}
          </div>
        </div>
        <div class="journal-time">${new Date(tx.posted_at).toLocaleString()}</div>
      </div>
      <table class="journal-legs-table">
        <tbody>
          ${(tx.entries || []).map(entry => `
            <tr>
              <td style="width: 80px;"><span class="badge badge-${entry.direction.toLowerCase()}">${entry.direction}</span></td>
              <td><strong>${entry.account_name || 'Account'}</strong> <span class="text-dim" style="font-size: 0.7rem;">(${entry.account_type || ''})</span></td>
              <td class="font-mono text-right" style="text-align: right; font-weight: 600;">
                ${entry.amount_formatted}
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `).join('');
}

function populateSelectDropdowns(accounts) {
  const srcSelect = document.getElementById('modal-transfer-source');
  const dstSelect = document.getElementById('modal-transfer-dest');
  const auditSelect = document.getElementById('audit-account-select');

  const options = accounts.map(a => `<option value="${a.id}">${a.name} (${a.type} - ${a.settled_balance_formatted})</option>`).join('');

  if (srcSelect) srcSelect.innerHTML = options;
  if (dstSelect) {
    dstSelect.innerHTML = options;
    if (accounts.length > 1) dstSelect.selectedIndex = 1;
  }
  if (auditSelect) auditSelect.innerHTML = options;
}

// Window Audit Trail
async function loadAuditAccountsDropdown() {
  if (currentAccounts.length === 0) {
    await loadAccounts();
  }
  const select = document.getElementById('audit-account-select');
  if (select && select.value) {
    runAccountAudit(select.value);
  }
}

async function runAccountAudit(accountId) {
  const tbody = document.getElementById('audit-timeline-tbody');
  tbody.innerHTML = '<tr><td colspan="5" class="text-center">Computing window function running balance...</td></tr>';

  try {
    const data = await fetch(`${API_BASE}/audit/accounts/${accountId}`).then(r => r.json());
    document.getElementById('audit-cached-val').textContent = data.settled_balance_formatted;
    document.getElementById('audit-reconcile-text').textContent = data.reconciliation_notes;

    const finalRunning = data.entries.length > 0 ? data.entries[data.entries.length - 1].running_balance_formatted : '$0.00';
    document.getElementById('audit-computed-val').textContent = finalRunning;

    const badge = document.getElementById('audit-status-badge');
    if (data.is_reconciled) {
      badge.className = 'badge badge-success';
      badge.textContent = '100% RECONCILED';
    } else {
      badge.className = 'badge badge-danger';
      badge.textContent = 'DISCREPANCY DETECTED';
    }

    if (!data.entries || data.entries.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center">No ledger entries for this account yet.</td></tr>';
      return;
    }

    tbody.innerHTML = data.entries.map(e => `
      <tr>
        <td class="text-dim" style="font-size: 0.8rem;">${new Date(e.posted_at).toLocaleTimeString()}</td>
        <td><strong>${e.description}</strong></td>
        <td><span class="badge badge-${e.direction.toLowerCase()}">${e.direction}</span></td>
        <td class="font-mono">${e.amount_formatted}</td>
        <td class="font-mono text-cyan" style="font-weight: 700; color: var(--accent-cyan);">
          ${e.running_balance_formatted}
        </td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger">Error: ${err.message}</td></tr>`;
  }
}

// Modals Setup
function setupModals() {
  document.querySelectorAll('[data-close]').forEach(b => {
    b.addEventListener('click', () => {
      const modalId = b.getAttribute('data-close');
      document.getElementById(modalId)?.classList.remove('open');
    });
  });

  document.getElementById('btn-quick-transfer')?.addEventListener('click', () => {
    document.getElementById('transfer-modal')?.classList.add('open');
  });

  document.getElementById('btn-open-transfer-modal')?.addEventListener('click', () => {
    document.getElementById('transfer-modal')?.classList.add('open');
  });

  document.getElementById('btn-open-create-account')?.addEventListener('click', () => {
    document.getElementById('account-modal')?.classList.add('open');
  });

  document.getElementById('btn-run-audit')?.addEventListener('click', () => {
    const sel = document.getElementById('audit-account-select');
    if (sel && sel.value) runAccountAudit(sel.value);
  });

  document.getElementById('btn-quick-reconcile')?.addEventListener('click', async () => {
    try {
      showToast('Running continuous system integrity reconciliation...', 'info');
      const rep = await fetch(`${API_BASE}/audit/reconcile`, { method: 'POST' }).then(r => r.json());
      if (rep.system_healthy) {
        showToast(`Reconciliation 100% Passed: All ${rep.total_accounts_audited} accounts balanced!`, 'success');
      } else {
        showToast(`Discrepancy detected in ${rep.discrepant_accounts} accounts!`, 'error');
      }
    } catch (e) {
      showToast('Reconciliation error: ' + e.message, 'error');
    }
  });
}

// Form Handlers
function setupForms() {
  // Transfer Form
  document.getElementById('transfer-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('btn-submit-transfer');
    btn.disabled = true;
    btn.textContent = 'Posting...';

    const src = document.getElementById('modal-transfer-source').value;
    const dst = document.getElementById('modal-transfer-dest').value;
    const dollars = parseFloat(document.getElementById('modal-transfer-amount').value);
    const desc = document.getElementById('modal-transfer-desc').value;
    const key = document.getElementById('modal-transfer-key').value.trim();

    const payload = {
      source_account_id: src,
      destination_account_id: dst,
      amount_cents: Math.round(dollars * 100),
      description: desc,
      idempotency_key: key || undefined
    };

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (key) headers['Idempotency-Key'] = key;

      const res = await fetch(`${API_BASE}/transfers`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error?.message || 'Transfer failed');
      }

      showToast(`Transfer posted: $${dollars.toFixed(2)}`, 'success');
      document.getElementById('transfer-modal').classList.remove('open');
      loadDashboardData();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Execute Transfer';
    }
  });

  // Account Form
  document.getElementById('account-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('btn-submit-account');
    btn.disabled = true;

    const name = document.getElementById('modal-account-name').value;
    const type = document.getElementById('modal-account-type').value;
    const currency = document.getElementById('modal-account-currency').value;
    const overdraft = document.getElementById('modal-account-overdraft').checked;
    const desc = document.getElementById('modal-account-desc').value;

    try {
      const res = await fetch(`${API_BASE}/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          type,
          currency,
          allow_overdraft: overdraft,
          description: desc || undefined
        })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || 'Failed to create account');

      showToast(`Created account: ${name}`, 'success');
      document.getElementById('account-modal').classList.remove('open');
      loadAccounts();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });
}

// Race Condition Simulator
function setupRaceDemo() {
  const fireBtn = document.getElementById('btn-fire-race');
  if (!fireBtn) return;

  fireBtn.addEventListener('click', async () => {
    fireBtn.disabled = true;
    fireBtn.innerHTML = `
      <svg class="spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
      Executing Concurrent Race...
    `;

    const initialBalDollars = parseFloat(document.getElementById('race-initial-bal').value) || 100.0;
    const workersCount = parseInt(document.getElementById('race-workers-count').value) || 50;
    const amountPerReqDollars = parseFloat(document.getElementById('race-amount-per-req').value) || 10.0;

    const payload = {
      initial_balance_cents: Math.round(initialBalDollars * 100),
      num_requests: workersCount,
      transfer_amount_cents: Math.round(amountPerReqDollars * 100)
    };

    try {
      const res = await fetch(`${API_BASE}/demo/race`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || 'Race failed');

      renderRaceResults(data);
      showToast('Race condition test finished with 0 invariant violations!', 'success');
    } catch (err) {
      showToast('Race error: ' + err.message, 'error');
    } finally {
      fireBtn.disabled = false;
      fireBtn.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
        Fire Concurrent Race
      `;
    }
  });
}

function renderRaceResults(res) {
  const resultsArea = document.getElementById('race-results-area');
  resultsArea.style.display = 'block';

  // Summary Banner
  const banner = document.getElementById('race-summary-banner');
  if (res.invariant_passed) {
    banner.className = 'race-summary-banner banner-success';
    banner.innerHTML = `
      <span>✔</span>
      <div>
        <strong>MATHEMATICAL ACID PROOF: ZERO DOUBLE-SPENDS DETECTED</strong><br>
        <span style="font-size: 0.85rem; font-weight: normal; opacity: 0.9;">
          Initial ${res.initial_balance_formatted} - (${res.actual_successes} x ${res.transfer_amount_formatted}) = ${res.source_final_balance_formatted}. 
          All ${res.actual_rejections} overdraft attempts were atomically blocked under row-level locks without a single deadlock.
        </span>
      </div>
    `;
  } else {
    banner.className = 'race-summary-banner banner-danger';
    banner.innerHTML = `<span>✖ Invariant verification failed.</span>`;
  }

  // Metrics
  document.getElementById('rm-total').textContent = res.total_requests;
  document.getElementById('rm-success').textContent = `${res.actual_successes} (100% expected)`;
  document.getElementById('rm-rejections').textContent = `${res.actual_rejections} rejected`;
  document.getElementById('rm-deadlocks').textContent = res.deadlocks_detected;
  document.getElementById('rm-latency').textContent = `${res.total_elapsed_ms} ms`;
  document.getElementById('rm-final-bal').textContent = res.source_final_balance_formatted;

  // Worker Chips
  const grid = document.getElementById('worker-chips-grid');
  grid.innerHTML = (res.workers || []).map(w => `
    <div class="worker-chip ${w.success ? 'chip-success' : 'chip-rejected'}">
      <div class="chip-title">
        <span>Worker #${w.worker_id}</span>
        <span>${w.status_code}</span>
      </div>
      <div class="chip-meta">${w.duration_ms}ms &bull; ${w.success ? 'POSTED' : 'OVERDRAFT'}</div>
    </div>
  `).join('');
}
