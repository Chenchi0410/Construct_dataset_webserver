const form = document.querySelector('#build-form');
const pdfInput = document.querySelector('#pdf-input');
const mdInput = document.querySelector('#md-input');
const pairing = document.querySelector('#pairing');
const result = document.querySelector('#result');
const metrics = document.querySelector('#metrics');
const documentsRoot = document.querySelector('#documents');
const toast = document.querySelector('#toast');
let activeSession = null;

function notify(message, error = false) {
  toast.textContent = message;
  toast.className = error ? 'show error' : 'show';
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { toast.className = ''; }, 4200);
}

function stems(files) {
  return new Map([...files].map(file => [file.name.replace(/\.[^.]+$/, '').toLowerCase(), file.name]));
}

function updatePairing() {
  const pdfs = stems(pdfInput.files);
  const mds = stems(mdInput.files);
  document.querySelector('#pdf-summary').textContent = pdfs.size ? `${pdfs.size} 个 PDF` : '支持单个或批量上传';
  document.querySelector('#md-summary').textContent = mds.size ? `${mds.size} 个 Markdown` : 'UTF-8 编码，名称需与 PDF 一致';
  if (!pdfs.size && !mds.size) { pairing.className = 'pairing hidden'; return; }
  const missingMd = [...pdfs.keys()].filter(key => !mds.has(key));
  const missingPdf = [...mds.keys()].filter(key => !pdfs.has(key));
  pairing.className = `pairing ${missingMd.length || missingPdf.length ? 'error' : 'ok'}`;
  if (!missingMd.length && !missingPdf.length) {
    pairing.textContent = `✓ 已识别 ${pdfs.size} 对同名文件`;
  } else {
    pairing.textContent = [missingMd.length ? `缺少 MD：${missingMd.join(', ')}` : '', missingPdf.length ? `缺少 PDF：${missingPdf.join(', ')}` : ''].filter(Boolean).join('；');
  }
}

[pdfInput, mdInput].forEach(input => input.addEventListener('change', updatePairing));
document.querySelectorAll('.dropzone').forEach(zone => {
  ['dragenter', 'dragover'].forEach(type => zone.addEventListener(type, () => zone.classList.add('drag')));
  ['dragleave', 'drop'].forEach(type => zone.addEventListener(type, () => zone.classList.remove('drag')));
});

function metric(value, label) { return `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`; }

function render(data) {
  activeSession = data;
  metrics.innerHTML = [
    metric(data.totals.documents, '文档'),
    metric(data.totals.rules, 'Sidecar 规则'),
    metric(data.totals.content_rules, '内容规则'),
    metric(data.totals.formatting_rules, '格式规则'),
    metric(data.totals.tables, '表格'),
  ].join('');
  documentsRoot.innerHTML = data.documents.map((doc, index) => `
    <details class="document" ${index === 0 ? 'open' : ''} data-stem="${escapeHtml(doc.stem)}">
      <summary>
        <div class="document-title"><strong>${escapeHtml(doc.pdf_name)}</strong><small>${escapeHtml(doc.md_name)}</small></div>
        <div class="rule-badges"><span>${doc.stats.content_rules} 内容</span><span>${doc.stats.formatting_rules} 格式</span><span>${doc.stats.tables} 表格</span></div>
      </summary>
      <div class="editor-wrap">
        <p>可直接编辑以下 Sidecar JSON；导出时将进行严格校验。</p>
        <textarea spellcheck="false">${escapeHtml(JSON.stringify(doc.sidecar, null, 2))}</textarea>
        ${doc.warnings.map(value => `<div class="warning">△ ${escapeHtml(value)}</div>`).join('')}
      </div>
    </details>`).join('');
  result.classList.remove('hidden');
  result.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  button.querySelector('span').textContent = '正在分析 Markdown…';
  try {
    const response = await fetch('/api/analyze', { method: 'POST', body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '分析失败');
    render(data);
    notify(`已生成 ${data.totals.rules} 条规则`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
    button.querySelector('span').textContent = '分析并生成规则';
  }
});

document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', async () => {
  if (!activeSession) return;
  const sidecars = {};
  try {
    document.querySelectorAll('.document').forEach(node => {
      sidecars[node.dataset.stem] = JSON.parse(node.querySelector('textarea').value);
    });
  } catch (error) {
    notify(`Sidecar JSON 格式错误：${error.message}`, true);
    return;
  }
  const mode = button.dataset.mode;
  button.disabled = true;
  try {
    const response = await fetch(`/api/export/${activeSession.session_id}?mode=${mode}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sidecars})
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.detail || '导出失败');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = match ? match[1] : `dataset_${mode}.zip`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    URL.revokeObjectURL(url);
    notify('测试集 ZIP 已生成');
  } catch (error) {
    notify(error.message, true);
  } finally { button.disabled = false; }
}));

