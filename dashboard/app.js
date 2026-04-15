const SUPABASE_URL = "https://nqzzrervkuocpgpmlikc.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5xenpyZXJ2a3VvY3BncG1saWtjIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjE2NDg2MSwiZXhwIjoyMDkxNzQwODYxfQ.6Do4pvJbLiWlDB9Rl_fgr895pXSEoDkpWVBFS0PrS24";
const supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

// Track chart instances to destroy on refresh
const charts = {};

// ─── Formatters ───────────────────────────────────────────────────────────────
const fmt = (v) => new Intl.NumberFormat('kk-KZ', { style: 'currency', currency: 'KZT', maximumFractionDigits: 0 }).format(v);
const fmtDate = (d) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(d));

// ─── Status badge ─────────────────────────────────────────────────────────────
const badge = (status) => {
    if (!status) return '<span class="order-status status-pending">Unknown</span>';
    const s = status.toLowerCase();
    if (['complete','assembled','delivered','completed'].includes(s)) return `<span class="order-status status-completed">${s}</span>`;
    if (['cancel-other','cancelled','fail'].includes(s)) return `<span class="order-status status-cancelled">${s}</span>`;
    return `<span class="order-status status-pending">${s}</span>`;
};

// ─── Toggle loader / chart ────────────────────────────────────────────────────
const showChart = (id) => {
    document.getElementById(`chart-loader-${id}`).style.display = 'none';
    document.getElementById(`chart-wrapper-${id}`).style.display = 'block';
};

// ─── Destroy old chart instance ───────────────────────────────────────────────
const destroyChart = (key) => { if (charts[key]) { charts[key].destroy(); delete charts[key]; } };

// ─── Common chart defaults ────────────────────────────────────────────────────
Chart.defaults.color = 'rgba(255,255,255,0.65)';
Chart.defaults.font.family = "'Outfit', sans-serif";

// ─── Color palette ────────────────────────────────────────────────────────────
const PALETTE = [
    '#00DFD8', '#FF6B9D', '#C77DFF', '#FFD166', '#06D6A0',
    '#EF476F', '#118AB2', '#FFB703', '#8338EC', '#FB5607'
];

// ─── Helper: aggregate array of objects by a key ──────────────────────────────
const aggregate = (arr, keyFn, valFn) => {
    const map = {};
    arr.forEach(item => {
        const k = keyFn(item) || 'Unknown';
        map[k] = (map[k] || 0) + valFn(item);
    });
    return Object.entries(map).sort((a, b) => b[1] - a[1]);
};

// ─── Main fetch & render ──────────────────────────────────────────────────────
async function fetchData() {
    try {
        // Reset all loaders
        ['revenue','products','channels','cities','channel-revenue','channel-aov'].forEach(id => {
            const loader = document.getElementById(`chart-loader-${id}`);
            const wrapper = document.getElementById(`chart-wrapper-${id}`);
            if (loader) { loader.style.display = 'flex'; loader.innerHTML = '<div class="spinner"></div>'; }
            if (wrapper) wrapper.style.display = 'none';
        });

        // Fetch orders
        const { data: orders, error: oErr } = await supabase
            .from('orders')
            .select('id, number, total_summ, created_at, status, city, utm_source')
            .order('created_at', { ascending: false });
        if (oErr) throw oErr;

        // Fetch order_items
        const { data: items, error: iErr } = await supabase
            .from('order_items')
            .select('id, order_id, offer_name, quantity, price');
        if (iErr) throw iErr;

        // ── KPIs ──────────────────────────────────────────────────────────────
        const validOrders = orders.filter(o => o.total_summ > 0 && !['cancel-other','cancelled'].includes((o.status||'').toLowerCase()));
        const totalRevenue = validOrders.reduce((s, o) => s + (o.total_summ || 0), 0);
        const aov = validOrders.length ? totalRevenue / validOrders.length : 0;
        document.getElementById('kpi-revenue').textContent = fmt(totalRevenue);
        document.getElementById('kpi-orders').textContent = orders.length.toLocaleString();
        document.getElementById('kpi-aov').textContent = fmt(aov);

        // ── Recent Activity ───────────────────────────────────────────────────
        document.getElementById('recent-orders-list').innerHTML = orders.slice(0, 6).map(o => `
            <li class="order-item">
                <div class="order-info">
                    <strong>Order ${o.number || `#${o.id}`}</strong>
                    <span>${fmtDate(o.created_at)}</span>
                    ${badge(o.status)}
                </div>
                <div class="order-amount">${fmt(o.total_summ || 0)}</div>
            </li>`).join('');

        // ── Render all charts ─────────────────────────────────────────────────
        renderRevenue(orders);
        renderProducts(items);
        renderChannels(orders);
        renderCities(orders);
        renderChannelRevenue(orders);
        renderChannelAov(orders);

    } catch (err) {
        console.error(err);
    }
}

// ── 1. Revenue Trajectory ─────────────────────────────────────────────────────
function renderRevenue(orders) {
    const agg = {};
    [...orders].sort((a,b) => new Date(a.created_at) - new Date(b.created_at)).forEach(o => {
        if ((o.total_summ||0) <= 0 || ['cancel-other','cancelled'].includes((o.status||'').toLowerCase())) return;
        const d = new Date(o.created_at).toLocaleDateString('en-US', { month:'short', day:'numeric' });
        agg[d] = (agg[d] || 0) + o.total_summ;
    });

    const ctx = document.getElementById('revenueChart').getContext('2d');
    const grad = ctx.createLinearGradient(0, 0, 0, 400);
    grad.addColorStop(0, 'rgba(0,223,216,0.3)');
    grad.addColorStop(1, 'rgba(0,223,216,0.0)');

    destroyChart('revenue');
    charts['revenue'] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: Object.keys(agg),
            datasets: [{ label: 'Revenue', data: Object.values(agg), borderColor: '#00DFD8', backgroundColor: grad, borderWidth: 3, pointBackgroundColor: '#00DFD8', pointBorderColor: '#fff', pointRadius: 4, pointHoverRadius: 8, fill: true, tension: 0.4 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(0,0,0,0.75)', borderColor: 'rgba(255,255,255,0.15)', borderWidth: 1, cornerRadius: 12, displayColors: false, callbacks: { label: c => fmt(c.parsed.y) } } }, scales: { x: { grid: { color: 'rgba(255,255,255,0.05)' } }, y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => (v/1000)+'k' } } }, interaction: { mode: 'index', intersect: false } }
    });
    showChart('revenue');
}

// ── 2. Revenue by Products (top 10 by item revenue) ──────────────────────────
function renderProducts(items) {
    const agg = aggregate(items, i => i.offer_name, i => (i.price || 0) * (i.quantity || 1));
    const top = agg.slice(0, 10);

    destroyChart('products');
    charts['products'] = new Chart(document.getElementById('productsChart'), {
        type: 'bar',
        data: {
            labels: top.map(([k]) => k.length > 20 ? k.slice(0, 20) + '…' : k),
            datasets: [{ label: 'Revenue ₸', data: top.map(([,v]) => v), backgroundColor: PALETTE, borderRadius: 6, borderSkipped: false }]
        },
        options: { responsive: true, maintainAspectRatio: false, indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmt(c.parsed.x) } } }, scales: { x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => (v/1000)+'k' } }, y: { grid: { display: false } } } }
    });
    showChart('products');
}

// ── 3. Acquisition Channels (orders count) ───────────────────────────────────
function renderChannels(orders) {
    const agg = aggregate(orders, o => o.utm_source, () => 1);

    destroyChart('channels');
    charts['channels'] = new Chart(document.getElementById('channelsChart'), {
        type: 'doughnut',
        data: {
            labels: agg.map(([k]) => k),
            datasets: [{ data: agg.map(([,v]) => v), backgroundColor: PALETTE, borderColor: 'rgba(255,255,255,0.05)', borderWidth: 2, hoverOffset: 10 }]
        },
        options: { responsive: true, maintainAspectRatio: false, cutout: '60%', plugins: { legend: { position: 'right', labels: { padding: 15, boxWidth: 12 } }, tooltip: { callbacks: { label: c => `${c.label}: ${c.parsed} orders` } } } }
    });
    showChart('channels');
}

// ── 4. Orders by City (top 10) ───────────────────────────────────────────────
function renderCities(orders) {
    const agg = aggregate(orders, o => o.city, () => 1).slice(0, 10);

    destroyChart('cities');
    charts['cities'] = new Chart(document.getElementById('citiesChart'), {
        type: 'bar',
        data: {
            labels: agg.map(([k]) => k),
            datasets: [{ label: 'Orders', data: agg.map(([,v]) => v), backgroundColor: PALETTE, borderRadius: 6 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.y} orders` } } }, scales: { x: { grid: { display: false } }, y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { stepSize: 1 } } } }
    });
    showChart('cities');
}

// ── 5. Revenue by Channel ─────────────────────────────────────────────────────
function renderChannelRevenue(orders) {
    const valid = orders.filter(o => (o.total_summ||0) > 0);
    const agg = aggregate(valid, o => o.utm_source, o => o.total_summ || 0);

    destroyChart('channel-revenue');
    charts['channel-revenue'] = new Chart(document.getElementById('channelRevenueChart'), {
        type: 'bar',
        data: {
            labels: agg.map(([k]) => k),
            datasets: [{ label: 'Revenue ₸', data: agg.map(([,v]) => v), backgroundColor: PALETTE, borderRadius: 6 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmt(c.parsed.y) } } }, scales: { x: { grid: { display: false } }, y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => (v/1000)+'k' } } } }
    });
    showChart('channel-revenue');
}

// ── 6. Avg Check by Channel ───────────────────────────────────────────────────
function renderChannelAov(orders) {
    const valid = orders.filter(o => (o.total_summ||0) > 0);

    // Group: sum and count per channel
    const groups = {};
    valid.forEach(o => {
        const k = o.utm_source || 'Unknown';
        if (!groups[k]) groups[k] = { sum: 0, count: 0 };
        groups[k].sum += o.total_summ || 0;
        groups[k].count += 1;
    });
    const agg = Object.entries(groups).map(([k, v]) => [k, v.sum / v.count]).sort((a,b) => b[1] - a[1]);

    destroyChart('channel-aov');
    charts['channel-aov'] = new Chart(document.getElementById('channelAovChart'), {
        type: 'bar',
        data: {
            labels: agg.map(([k]) => k),
            datasets: [{ label: 'Avg ₸', data: agg.map(([,v]) => v), backgroundColor: PALETTE, borderRadius: 6 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmt(c.parsed.y) } } }, scales: { x: { grid: { display: false } }, y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => (v/1000)+'k' } } } }
    });
    showChart('channel-aov');
}

window.fetchData = fetchData;
document.addEventListener('DOMContentLoaded', fetchData);
