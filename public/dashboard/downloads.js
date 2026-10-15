/* Verify both gzip transport and original bytes before saving a bulk export. */
document.addEventListener('click', async event => {
  const link = event.target.closest('a[data-original-name]');
  if (!link) return;
  if (!globalThis.DecompressionStream || !globalThis.crypto?.subtle) {
    link.download = `${link.dataset.originalName}.gz`;
    alert('このブラウザではgzipファイルを保存します。Macではダブルクリックで元のCSVに解凍できます。');
    return;
  }
  event.preventDefault();
  const digest = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
  link.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(link.href, {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const packed = await response.arrayBuffer();
    if (await digest(packed) !== link.dataset.packedSha256) throw new Error('gzip SHA-256');
    const raw = await new Response(new Blob([packed]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    if (raw.byteLength !== Number(link.dataset.originalBytes) || await digest(raw) !== link.dataset.originalSha256) throw new Error('original SHA-256');
    const url = URL.createObjectURL(new Blob([raw], {type: 'text/csv;charset=utf-8'}));
    const save = document.createElement('a');
    save.href = url;
    save.download = link.dataset.originalName;
    save.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    alert(`ダウンロードの検証に失敗しました。保存せず停止しました（${error.message}）。再読み込みしてお試しください。`);
  } finally {
    link.removeAttribute('aria-busy');
  }
});
