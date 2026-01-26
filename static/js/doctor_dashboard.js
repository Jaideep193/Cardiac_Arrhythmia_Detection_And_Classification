// Preview modal handling
const previewModal = new bootstrap.Modal(document.getElementById('previewModal'));
function clearSearch(){ document.getElementById('searchBox').value=''; filterTable(); }
function filterTable(){
  const q = document.getElementById('searchBox').value.toLowerCase();
  const resultFilter = document.getElementById('resultTypeFilter')?.value || 'all';
  const rows = document.querySelectorAll('#reportsTable tbody tr');
  
  rows.forEach(r => {
    const text = r.innerText.toLowerCase();
    const matchesSearch = text.indexOf(q) !== -1;
    
    // Check result type filter
    let matchesResultType = true;
    if (resultFilter !== 'all') {
      const resultData = r.getAttribute('data-result') || '';
      const diagnosisCell = r.querySelector('td:nth-child(6)');
      const hasNormalBadge = diagnosisCell?.querySelector('.badge-normal');
      const hasAbnormalBadge = diagnosisCell?.querySelector('.badge-abnormal');
      
      if (resultFilter === 'normal') {
        matchesResultType = hasNormalBadge || resultData.includes('normal') || resultData === '1';
      } else if (resultFilter === 'abnormal') {
        matchesResultType = hasAbnormalBadge || (!resultData.includes('normal') && resultData !== '1' && resultData !== '');
      }
    }
    
    r.style.display = (matchesSearch && matchesResultType) ? '' : 'none';
  });
  
  // Recompute stats after filtering
  computeStats();
}

function applyClientFilter(){
  filterTable();
}

// Preview modal handling
document.addEventListener('click', function(e){
  const el = e.target.closest('.preview-link');
  if(!el) return;
  e.preventDefault();
  const src = el.getAttribute('data-src');
  const type = el.getAttribute('data-type');
  const img = document.getElementById('previewImg');
  const pdf = document.getElementById('previewPdf');
  if(type==='pdf'){
    img.style.display='none'; pdf.style.display='block'; pdf.src = src;
  } else {
    pdf.style.display='none'; img.style.display='block'; img.src = src;
  }
  // focus management: remember opener, open modal, then focus close
  window.__lastFocusedElement = document.activeElement;
  previewModal.show();
  const closeBtn = document.getElementById('previewCloseBtn'); if(closeBtn) closeBtn.focus();
});

// restore focus when modal closes
document.getElementById('previewModal')?.addEventListener('hidden.bs.modal', function(){
  try{ if(window.__lastFocusedElement) window.__lastFocusedElement.focus(); }catch(e){}
});

// keyboard activation for preview links and action buttons
function enableKeyboardActivation(selector){
  document.querySelectorAll(selector).forEach(el=>{
    el.addEventListener('keydown', function(ev){
      if(ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar'){
        ev.preventDefault(); el.click();
      }
    });
  });
}
enableKeyboardActivation('.preview-link');
enableKeyboardActivation('.action-btn, .btn[role="button"]');

// === Dashboard Customization Panel (Enhanced) ===
let dashboardLayout = {};
let draggedItem = null;

function initializeCustomizePanel() {
  const toggleBtn = document.getElementById('toggleCustomizeBtn');
  const closeBtn = document.getElementById('closeCustomizeBtn');
  const resetBtn = document.getElementById('resetLayoutBtn');
  const panel = document.getElementById('customizePanel');

  if (!toggleBtn || !closeBtn || !resetBtn || !panel) {
    console.warn('⚠️ Customize panel elements not found');
    return;
  }

  // Toggle button: open panel
  toggleBtn.addEventListener('click', (e) => {
    e.preventDefault();
    openCustomizePanel();
  });

  // Close button: close panel
  closeBtn.addEventListener('click', (e) => {
    e.preventDefault();
    closeCustomizePanel();
  });

  // Reset button: reset layout
  resetBtn.addEventListener('click', (e) => {
    e.preventDefault();
    if (confirm('Reset dashboard to default layout?')) {
      dashboardLayout = getDefaultLayout();
      saveDashboardLayout();
      location.reload();
    }
  });

  // Overlay click: close panel
  document.addEventListener('click', (e) => {
    if (e.target.id === 'customizeOverlay') {
      closeCustomizePanel();
    }
  });

  // Load and apply layout
  loadDashboardLayout();
  setupDragDrop();
  setupColumnCheckboxes();
}

function getDefaultLayout() {
  return {
    componentOrder: ['stats', 'filters', 'table', 'pagination'],
    visibleColumns: ['patient', 'file', 'preview', 'created', 'result', 'class', 'actions']
  };
}

function loadDashboardLayout() {
  const saved = localStorage.getItem('dashboardLayout');
  if (saved) {
    try {
      dashboardLayout = JSON.parse(saved);
      console.log('📦 Loaded layout from localStorage:', dashboardLayout);
    } catch (e) {
      console.warn('⚠️ Failed to parse saved layout:', e);
      dashboardLayout = getDefaultLayout();
    }
  } else {
    dashboardLayout = getDefaultLayout();
  }
  
  // Apply the loaded layout immediately
  applyDashboardLayout();
}

function saveDashboardLayout() {
  localStorage.setItem('dashboardLayout', JSON.stringify(dashboardLayout));
}

function openCustomizePanel() {
  const panel = document.getElementById('customizePanel');
  if (!panel) return;

  // Create or show overlay
  let overlay = document.getElementById('customizeOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'customizeOverlay';
    overlay.className = 'customize-overlay';
    document.body.appendChild(overlay);
  }

  panel.classList.add('active');
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeCustomizePanel() {
  const panel = document.getElementById('customizePanel');
  const overlay = document.getElementById('customizeOverlay');

  if (panel) panel.classList.remove('active');
  if (overlay) overlay.classList.remove('active');
  document.body.style.overflow = '';
}

function setupDragDrop() {
  const customizeList = document.getElementById('customizeList');
  if (!customizeList) return;

  const items = customizeList.querySelectorAll('.customize-item');

  items.forEach((item) => {
    item.addEventListener('dragstart', (e) => {
      draggedItem = item;
      item.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });

    item.addEventListener('dragend', (e) => {
      item.classList.remove('dragging');
      items.forEach((i) => i.classList.remove('drag-over'));
    });

    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (item !== draggedItem) {
        item.classList.add('drag-over');
      }
    });

    item.addEventListener('dragleave', (e) => {
      item.classList.remove('drag-over');
    });

    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drag-over');

      if (draggedItem && draggedItem !== item) {
        const allItems = Array.from(customizeList.children);
        const draggedIndex = allItems.indexOf(draggedItem);
        const targetIndex = allItems.indexOf(item);

        if (draggedIndex < targetIndex) {
          item.parentNode.insertBefore(draggedItem, item.nextSibling);
        } else {
          item.parentNode.insertBefore(draggedItem, item);
        }

        // Update layout and apply immediately to dashboard
        const newOrder = Array.from(customizeList.querySelectorAll('.customize-item')).map((el) =>
          el.getAttribute('data-component')
        );
        dashboardLayout.componentOrder = newOrder;
        saveDashboardLayout();
        applyDashboardLayout();
        console.log('✅ Components reordered:', newOrder);
      }
    });
  });
}

function setupColumnCheckboxes() {
  const columnCheckboxes = document.querySelectorAll('#customizeColumns input[type="checkbox"]');

  columnCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      const columns = Array.from(columnCheckboxes)
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);

      dashboardLayout.visibleColumns = columns;
      saveDashboardLayout();
      applyColumnVisibility();
      console.log('✅ Column visibility updated:', columns);
    });
  });
  
  // Apply visibility on page load
  applyColumnVisibility();
}

// Apply column visibility to table
function applyColumnVisibility() {
  const visibleColumns = dashboardLayout.visibleColumns || ['patient', 'file', 'preview', 'created', 'result', 'class', 'actions'];
  
  // Column mapping: checkbox value -> column index (0-based, excluding #)
  const columnMap = {
    'patient': 1,
    'file': 2,
    'preview': 3,
    'created': 4,
    'result': 5,
    'class': 6,
    'actions': 7
  };
  
  const table = document.getElementById('reportsTable');
  if (!table) return;
  
  // Hide/show header columns
  const headerCells = table.querySelectorAll('thead th');
  Object.keys(columnMap).forEach(col => {
    const index = columnMap[col];
    const isVisible = visibleColumns.includes(col);
    if (headerCells[index]) {
      headerCells[index].style.display = isVisible ? '' : 'none';
    }
  });
  
  // Hide/show body columns
  const rows = table.querySelectorAll('tbody tr');
  rows.forEach(row => {
    const cells = row.querySelectorAll('td');
    Object.keys(columnMap).forEach(col => {
      const index = columnMap[col];
      const isVisible = visibleColumns.includes(col);
      if (cells[index]) {
        cells[index].style.display = isVisible ? '' : 'none';
      }
    });
  });
  
  console.log('✅ Columns visible:', visibleColumns);
}

// Initialize customize panel when DOM is ready
document.addEventListener('DOMContentLoaded', initializeCustomizePanel);

// Apply actual dashboard reordering based on stored component order
function applyDashboardLayout() {
  const container = document.getElementById('dashboardLayout');
  if (!container) {
    console.warn('⚠️ dashboardLayout container not found');
    return;
  }
  
  const componentOrder = dashboardLayout.componentOrder || ['stats', 'filters', 'table', 'pagination'];
  const components = {};
  
  // Collect all dashboard components by their data-component attribute
  container.querySelectorAll('.dashboard-component').forEach(comp => {
    const name = comp.getAttribute('data-component');
    if (name) {
      components[name] = comp;
    }
  });
  
  // Reorder by appending in the correct order (this removes and re-adds elements)
  componentOrder.forEach((compName) => {
    const comp = components[compName];
    if (comp) {
      container.appendChild(comp);
    }
  });
  
  console.log('✅ Dashboard reordered to:', componentOrder);
}

// Compute top stats client-side from rendered rows (no backend change required)
function computeStats(){
  const rows = document.querySelectorAll('#reportsTable tbody tr');
  let total = 0, today = 0, normal = 0, abnormal = 0;
  const todayStr = new Date().toISOString().slice(0,10);
  rows.forEach(r=>{
    // skip rows hidden by client-side filter
    if (r.style.display === 'none') return;
    total += 1;
    const createdCell = r.querySelector('td:nth-child(5)');
    const created = createdCell ? createdCell.innerText.trim() : '';
    if(created.includes(todayStr)) today++;

    // detect badge by class - check for both .badge and .badge-pill classes
    const diagnosisCell = r.querySelector('td:nth-child(6)');
    const badge = diagnosisCell ? diagnosisCell.querySelector('.badge-normal, .badge-abnormal, .badge') : null;
    
    if(badge){
      if(badge.classList.contains('badge-normal')) {
        normal++;
      } else if(badge.classList.contains('badge-abnormal')) {
        abnormal++;
      } else {
        // fallback to text check for other badge types
        const txt = badge.innerText.toLowerCase();
        if(txt.includes('normal')) normal++; 
        else if(txt.includes('abnormal') || txt.includes('attention')) abnormal++;
      }
    } else if(diagnosisCell) {
      // fallback: check the cell text directly
      const txt = diagnosisCell.innerText.toLowerCase();
      if(txt.includes('normal') && !txt.includes('abnormal')) normal++; 
      else if(txt.includes('abnormal') || txt.includes('attention')) abnormal++;
    }
  });
  const statTotal = document.getElementById('statTotal');
  // animate counts for nicer UX
  animateCount(statTotal, total);
  animateCount(document.getElementById('statToday'), today);
  animateCount(document.getElementById('statNormal'), normal);
  animateCount(document.getElementById('statAbnormal'), abnormal);
}

// Initialize
document.addEventListener('DOMContentLoaded', ()=>{
  // initialize Bootstrap tooltips for class buttons
  try{
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(function (el) { new bootstrap.Tooltip(el); });
  }catch(e){ /* ignore if bootstrap not available */ }
  // add subtle pulse to stat cards on load
  document.querySelectorAll('.stat-card').forEach((c,i)=>{ setTimeout(()=>c.classList.add('pulse'), i*150); });
  computeStats();
  // reveal visible rows with stagger
  const visible = Array.from(document.querySelectorAll('#reportsTable tbody tr')).filter(r=> r.style.display !== 'none');
  visible.forEach((r,idx)=>{ r.classList.add('row-fade'); r.style.animationDelay = (idx*40)+'ms'; setTimeout(()=> r.classList.remove('row-fade'), 800); });
});

// animate number from current to target
function animateCount(el, to){
  if(!el) return;
  const start = parseInt(el.dataset._count_current || el.innerText.replace(/[^0-9]/g,'') || 0,10) || 0;
  const end = parseInt(to,10) || 0;
  const duration = 600;
  const startTime = performance.now();
  function step(now){
    const p = Math.min(1, (now-startTime)/duration);
    const val = Math.round(start + (end-start) * easeOutCubic(p));
    el.innerText = val;
    if(p < 1) requestAnimationFrame(step); else el.dataset._count_current = end;
  }
  requestAnimationFrame(step);
}
function easeOutCubic(t){ return 1 - Math.pow(1-t,3); }

// === Visualization Builder ===
let selectedChartType = 'bar';
let xAxisField = null;
let yAxisField = null;
let mainChartInstance = null;
let draggedField = null;

function initializeVisualizationBuilder() {
  // Chart type selection
  document.querySelectorAll('.chart-type-option').forEach(option => {
    option.addEventListener('click', function() {
      document.querySelectorAll('.chart-type-option').forEach(o => o.classList.remove('active'));
      this.classList.add('active');
      selectedChartType = this.getAttribute('data-type');
      console.log('📊 Chart type selected:', selectedChartType);
    });
  });
  
  // Field drag and drop
  const fields = document.querySelectorAll('.field-item');
  const dropZones = document.querySelectorAll('.drop-zone');
  
  fields.forEach(field => {
    field.addEventListener('dragstart', (e) => {
      draggedField = e.target;
      e.target.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('field', e.target.getAttribute('data-field'));
    });
    
    field.addEventListener('dragend', (e) => {
      e.target.classList.remove('dragging');
    });
  });
  
  dropZones.forEach(zone => {
    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      zone.classList.add('drag-over');
    });
    
    zone.addEventListener('dragleave', () => {
      zone.classList.remove('drag-over');
    });
    
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      
      const fieldName = e.dataTransfer.getData('field');
      const fieldText = draggedField.textContent;
      const zoneType = zone.getAttribute('data-zone');
      
      // Update zone display
      zone.classList.add('has-field');
      zone.innerHTML = `<i class=\"fa fa-check-circle\"></i><span>${fieldText}</span>`;
      
      // Store selection
      if (zoneType === 'x') {
        xAxisField = fieldName;
      } else {
        yAxisField = fieldName;
      }
      
      console.log('✅ Field dropped:', fieldName, 'on', zoneType, 'axis');
    });
  });
  
  // Generate chart button
  document.getElementById('generateChartBtn')?.addEventListener('click', generateChart);
}

function generateChart() {
  if (!xAxisField || !yAxisField) {
    alert('⚠️ Please drag fields to both X-Axis and Y-Axis zones');
    return;
  }
  
  // Get data from table
  const chartData = extractChartData(xAxisField, yAxisField);
  
  if (!chartData || chartData.labels.length === 0) {
    alert('⚠️ No data available for selected fields');
    return;
  }
  
  // Show chart container
  document.getElementById('chartPlaceholder').style.display = 'none';
  document.getElementById('mainChartContainer').style.display = 'block';
  
  // Destroy existing chart
  if (mainChartInstance) {
    mainChartInstance.destroy();
  }
  
  // Create new chart
  const ctx = document.getElementById('mainChart').getContext('2d');
  mainChartInstance = new Chart(ctx, {
    type: selectedChartType,
    data: {
      labels: chartData.labels,
      datasets: [{
        label: chartData.label,
        data: chartData.values,
        backgroundColor: generateColors(chartData.values.length, 0.6),
        borderColor: generateColors(chartData.values.length, 1),
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          display: ['pie', 'doughnut'].includes(selectedChartType),
          position: 'bottom'
        },
        title: {
          display: true,
          text: `${chartData.label} by ${xAxisField.replace('_', ' ').toUpperCase()}`,
          font: { size: 16, weight: 'bold' }
        }
      },
      scales: ['bar', 'line'].includes(selectedChartType) ? {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      } : {}
    }
  });
  
  console.log('✅ Chart generated:', selectedChartType, chartData);
}

function extractChartData(xField, yField) {
  const table = document.getElementById('reportsTable');
  if (!table) return null;
  
  const rows = table.querySelectorAll('tbody tr');
  const dataMap = {};
  
  rows.forEach(row => {
    if (row.style.display === 'none') return; // Skip filtered rows
    
    let xValue = '';
    let yValue = 1; // Count by default
    
    // Extract X value
    if (xField === 'patient_name') {
      xValue = row.cells[1]?.textContent.trim() || 'Unknown';
    } else if (xField === 'result') {
      xValue = row.querySelector('td:nth-child(6) .badge')?.textContent.trim() || 'Unknown';
    } else if (xField === 'class') {
      xValue = 'Class ' + (row.cells[6]?.textContent.trim() || '?');
    } else if (xField === 'created_at') {
      const date = row.cells[4]?.textContent.trim() || '';
      xValue = date.split(' ')[0] || 'Unknown'; // Get date part
    } else if (xField === 'file_type') {
      const filename = row.cells[2]?.textContent.trim() || '';
      xValue = filename.split('.').pop()?.toUpperCase() || 'Unknown';
    }
    
    // Aggregate data
    if (!dataMap[xValue]) {
      dataMap[xValue] = 0;
    }
    dataMap[xValue]++;
  });
  
  return {
    labels: Object.keys(dataMap),
    values: Object.values(dataMap),
    label: yField === 'count' ? 'Count' : yField.replace('_', ' ').toUpperCase()
  };
}

function generateColors(count, alpha) {
  const colors = [
    `rgba(13, 110, 253, ${alpha})`,   // Blue
    `rgba(25, 135, 84, ${alpha})`,    // Green
    `rgba(220, 53, 69, ${alpha})`,    // Red
    `rgba(255, 193, 7, ${alpha})`,    // Yellow
    `rgba(13, 202, 240, ${alpha})`,   // Cyan
    `rgba(111, 66, 193, ${alpha})`,   // Purple
    `rgba(255, 99, 132, ${alpha})`,   // Pink
    `rgba(54, 162, 235, ${alpha})`    // Light Blue
  ];
  return colors.slice(0, count);
}

// Initialize visualization builder on load
document.addEventListener('DOMContentLoaded', () => {
  initializeVisualizationBuilder();
});

