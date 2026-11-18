"use strict";

const PAGE_WIDTH = 595.28;
const MAX_PAGE_HEIGHT = 14400;

const shootBtn = document.querySelector("#shoot");
const saveBtn = document.querySelector("#save");
const stackEl = document.querySelector("#stack");
const statusEl = document.querySelector("#status");
const countEl = document.querySelector("#count");
const frameVideo = document.createElement("video");
frameVideo.muted = true;
frameVideo.autoplay = true;
frameVideo.playsInline = true;
frameVideo.hidden = true;
document.body.append(frameVideo);

let stream = null;
let shots = [];
let seq = 1;
let shooting = false;
let saving = false;
let picking = false;
let picker = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function printsLabel(count) {
  return count === 1 ? "1 print" : `${count} prints`;
}

function isLive() {
  return !!(stream && stream.getVideoTracks().some((track) => track.readyState === "live"));
}

function updateControls() {
  shootBtn.disabled = shooting || picking;
  saveBtn.disabled = shots.length === 0 || saving;
  countEl.textContent = `Caderno · ${printsLabel(shots.length)}`;
}

function render({ scroll = false } = {}) {
  updateControls();
  if (shots.length === 0) {
    stackEl.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum print ainda. Use Tirar print e arraste um retângulo.";
    stackEl.append(empty);
    return;
  }

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  shots.forEach((shot, index) => {
    const number = index + 1;
    const article = document.createElement("article");
    article.className = "shot";

    const num = document.createElement("p");
    num.className = "num";
    num.setAttribute("aria-hidden", "true");
    num.textContent = String(number);

    const frame = document.createElement("div");
    frame.className = "frame";

    const img = document.createElement("img");
    img.src = shot.url;
    img.alt = `Print ${number}`;
    img.width = shot.width;
    img.height = shot.height;
    img.draggable = false;

    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.textContent = "Excluir";
    del.setAttribute("aria-label", `Excluir print ${number}`);
    del.addEventListener("click", () => removeShot(shot.id));

    frame.append(img, del);
    article.append(num, frame);
    sheet.append(article);
  });
  stackEl.replaceChildren(sheet);
  if (!scroll) return;
  const snap = () => {
    const last = sheet.querySelector(".shot:last-child");
    if (last) last.scrollIntoView({ block: "end", inline: "nearest" });
  };
  snap();
  requestAnimationFrame(snap);
  const lastImg = sheet.querySelector("img:last-of-type");
  if (lastImg && !lastImg.complete) lastImg.addEventListener("load", snap, { once: true });
}

function removeShot(id) {
  const index = shots.findIndex((shot) => shot.id === id);
  if (index < 0) return;
  URL.revokeObjectURL(shots[index].url);
  shots.splice(index, 1);
  render();
  const buttons = stackEl.querySelectorAll(".del");
  const next = buttons[Math.min(index, buttons.length - 1)];
  if (next) next.focus();
  else stackEl.focus();
  setStatus(shots.length === 0 ? "Print excluído." : `${printsLabel(shots.length)} na pilha.`);
}

function releaseStream() {
  const current = stream;
  stream = null;
  if (!current) return;
  for (const track of current.getTracks()) track.stop();
  if (frameVideo.srcObject === current) frameVideo.srcObject = null;
}

function attachStream(next) {
  const previous = stream;
  stream = next;
  if (previous) {
    for (const track of previous.getTracks()) track.stop();
  }
  frameVideo.srcObject = next;
  const finish = () => {
    if (stream !== next) return;
    if (next.getVideoTracks().some((item) => item.readyState === "live")) return;
    releaseStream();
    closePicker();
    updateControls();
    setStatus("A tela foi encerrada. Tire um print para escolher de novo.");
  };
  next.addEventListener("inactive", finish);
  const track = next.getVideoTracks()[0];
  if (track) track.addEventListener("ended", finish);
  frameVideo.play().catch(() => {});
  updateControls();
}

async function ensureCapture() {
  if (isLive()) return true;
  if (!window.isSecureContext) {
    setStatus("Abra esta página em HTTPS para tirar um print.");
    return false;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    setStatus("Este navegador não permite tirar um print da tela.");
    return false;
  }
  try {
    const next = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false
    });
    attachStream(next);
    return isLive();
  } catch (err) {
    const name = err && err.name;
    if (name === "NotAllowedError" || name === "AbortError") {
      setStatus(isLive() ? "A tela anterior continua ativa." : "Print cancelado.");
    } else {
      setStatus("Não foi possível acessar a tela.");
    }
    return false;
  }
}

function waitForFrame() {
  if (frameVideo.videoWidth && frameVideo.videoHeight) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      frameVideo.removeEventListener("resize", ready);
      frameVideo.removeEventListener("loadeddata", ready);
      reject(new Error("frame"));
    }, 4000);
    const ready = () => {
      if (!frameVideo.videoWidth || !frameVideo.videoHeight) return;
      window.clearTimeout(timer);
      frameVideo.removeEventListener("resize", ready);
      frameVideo.removeEventListener("loadeddata", ready);
      resolve();
    };
    frameVideo.addEventListener("resize", ready);
    frameVideo.addEventListener("loadeddata", ready);
  });
}

function grabFrame() {
  const width = frameVideo.videoWidth;
  const height = frameVideo.videoHeight;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.drawImage(frameVideo, 0, 0, width, height);
  return canvas;
}

async function storeCanvas(canvas) {
  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob((result) => {
      if (result) resolve(result);
      else reject(new Error("blob"));
    }, "image/jpeg", 0.95);
  });
  if (blob.type !== "image/jpeg") throw new Error("tipo");
  const bytes = new Uint8Array(await blob.arrayBuffer());
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("jpeg");
  const url = URL.createObjectURL(blob);
  shots.push({ id: seq++, url, width: canvas.width, height: canvas.height, bytes });
  render({ scroll: true });
}

function closePicker() {
  if (picker) {
    if (picker.cleanup) picker.cleanup();
    picker.remove();
    picker = null;
  }
  picking = false;
}

function pointOnFrame(event, view, frame) {
  const bounds = view.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const x = ((event.clientX - bounds.left) * frame.width) / bounds.width;
  const y = ((event.clientY - bounds.top) * frame.height) / bounds.height;
  return {
    x: Math.min(frame.width, Math.max(0, x)),
    y: Math.min(frame.height, Math.max(0, y))
  };
}

function openPicker(frame) {
  closePicker();
  picking = true;
  updateControls();
  const overlay = document.createElement("div");
  overlay.className = "picker";
  overlay.tabIndex = -1;
  const stage = document.createElement("div");
  stage.className = "picker-stage";
  const view = document.createElement("canvas");
  view.className = "picker-view";
  view.width = frame.width;
  view.height = frame.height;
  view.getContext("2d").drawImage(frame, 0, 0);
  const box = document.createElement("div");
  box.className = "picker-box";
  box.hidden = true;
  const hint = document.createElement("p");
  hint.className = "picker-hint";
  hint.textContent = "Arraste um retângulo · Esc cancela";
  stage.append(view, box);
  overlay.append(stage, hint);
  document.body.append(overlay);
  overlay.focus();
  picker = overlay;

  let start = null;
  let dragging = false;
  let settled = false;

  const drawBox = (a, b) => {
    const bounds = view.getBoundingClientRect();
    const stageBounds = stage.getBoundingClientRect();
    const left = (Math.min(a.x, b.x) / frame.width) * bounds.width;
    const top = (Math.min(a.y, b.y) / frame.height) * bounds.height;
    const width = (Math.abs(b.x - a.x) / frame.width) * bounds.width;
    const height = (Math.abs(b.y - a.y) / frame.height) * bounds.height;
    box.hidden = false;
    box.style.left = `${bounds.left - stageBounds.left + left}px`;
    box.style.top = `${bounds.top - stageBounds.top + top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
  };

  const onKey = (event) => {
    if (event.key !== "Escape" || settled) return;
    event.preventDefault();
    closePicker();
    updateControls();
    setStatus("Print cancelado.");
  };

  const onDown = (event) => {
    if (settled || event.button !== 0) return;
    const point = pointOnFrame(event, view, frame);
    if (!point) return;
    dragging = true;
    start = point;
    overlay.setPointerCapture(event.pointerId);
    drawBox(start, start);
    event.preventDefault();
  };

  const onMove = (event) => {
    if (!dragging || !start || settled) return;
    const point = pointOnFrame(event, view, frame);
    if (!point) return;
    drawBox(start, point);
  };

  const finish = async (event) => {
    if (settled || !dragging || !start) return;
    dragging = false;
    const end = pointOnFrame(event, view, frame);
    if (!end) return;
    const x = Math.round(Math.min(start.x, end.x));
    const y = Math.round(Math.min(start.y, end.y));
    const width = Math.round(Math.abs(end.x - start.x));
    const height = Math.round(Math.abs(end.y - start.y));
    start = null;
    if (width < 8 || height < 8) {
      box.hidden = true;
      return;
    }
    const sx = Math.max(0, Math.min(frame.width - 1, x));
    const sy = Math.max(0, Math.min(frame.height - 1, y));
    const sw = Math.max(1, Math.min(frame.width - sx, width));
    const sh = Math.max(1, Math.min(frame.height - sy, height));
    settled = true;
    closePicker();
    const crop = document.createElement("canvas");
    crop.width = sw;
    crop.height = sh;
    const ctx = crop.getContext("2d", { alpha: false });
    ctx.drawImage(frame, sx, sy, sw, sh, 0, 0, sw, sh);
    shooting = true;
    updateControls();
    setStatus("A capturar…");
    try {
      await storeCanvas(crop);
      setStatus(`Print ${shots.length} guardado no fim da pilha.`);
    } catch (err) {
      setStatus("Não foi possível guardar este print.");
    } finally {
      shooting = false;
      updateControls();
    }
  };

  overlay.addEventListener("pointerdown", onDown);
  overlay.addEventListener("pointermove", onMove);
  overlay.addEventListener("pointerup", finish);
  overlay.addEventListener("pointercancel", () => {
    dragging = false;
    start = null;
    box.hidden = true;
  });
  document.addEventListener("keydown", onKey);
  overlay.cleanup = () => document.removeEventListener("keydown", onKey);
  setStatus("Arraste um retângulo na imagem. Esc cancela.");
}

async function tirarPrint() {
  if (picking || shooting) return;
  if (!isLive()) {
    const ok = await ensureCapture();
    if (!ok) return;
  }
  try {
    await frameVideo.play().catch(() => {});
    await waitForFrame();
  } catch (err) {
    setStatus("A imagem da tela ainda não chegou. Tente de novo.");
    return;
  }
  const frame = grabFrame();
  if (!frame.width || !frame.height) {
    setStatus("A imagem da tela ainda não chegou. Tente de novo.");
    return;
  }
  openPicker(frame);
}

function fmt(value) {
  if (!Number.isFinite(value)) throw new Error("número inválido");
  const rounded = Math.round(value * 1000) / 1000;
  if (rounded === 0) return "0";
  return rounded.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function layoutPages(list) {
  const pages = [];
  let page = { height: 0, items: [] };
  let guard = 0;

  const flush = () => {
    if (!page.items.length) return;
    pages.push(page);
    page = { height: 0, items: [] };
  };

  for (const shot of list) {
    if (!(shot.width > 0) || !(shot.height > 0)) throw new Error("dimensão");
    const fullH = PAGE_WIDTH * (shot.height / shot.width);
    let consumed = 0;
    while (consumed < 1 - 1e-8) {
      if (++guard > 100000) throw new Error("layout");
      const remainH = fullH * (1 - consumed);
      if (remainH <= 0.05) break;
      const space = MAX_PAGE_HEIGHT - page.height;
      if (page.items.length > 0 && remainH > space + 0.05 && remainH <= MAX_PAGE_HEIGHT) {
        flush();
        continue;
      }
      if (space <= 0.05) {
        flush();
        continue;
      }
      const takeH = Math.min(space, remainH);
      const takeFrac = takeH / fullH;
      page.items.push({
        shot,
        srcTop: consumed,
        srcBottom: consumed + takeFrac,
        top: page.height,
        fullH
      });
      page.height += takeH;
      consumed += takeFrac;
      if (page.height >= MAX_PAGE_HEIGHT - 0.05) flush();
    }
  }
  flush();
  return pages;
}

function concat(parts) {
  let size = 0;
  for (const part of parts) size += part.length;
  const out = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function ascii(text) {
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code > 255) throw new Error("ascii");
    out[i] = code;
  }
  return out;
}

function contentFor(page, indexByShot) {
  let text = "";
  for (const item of page.items) {
    const name = `/Im${indexByShot.get(item.shot)}`;
    const slotH = (item.srcBottom - item.srcTop) * item.fullH;
    const imageTop = item.top - item.srcTop * item.fullH;
    const y = page.height - imageTop - item.fullH;
    const slotBottom = page.height - item.top - slotH;
    text += "q\n";
    text += `${fmt(0)} ${fmt(slotBottom)} ${fmt(PAGE_WIDTH)} ${fmt(slotH)} re\n`;
    text += "W\nn\n";
    text += `${fmt(PAGE_WIDTH)} 0 0 ${fmt(item.fullH)} 0 ${fmt(y)} cm\n`;
    text += `${name} Do\n`;
    text += "Q\n";
  }
  return text;
}

function buildPdf(list) {
  const pages = layoutPages(list);
  if (!pages.length) throw new Error("vazio");

  const indexByShot = new Map(list.map((shot, index) => [shot, index]));
  let nextId = 1;
  const catalogId = nextId++;
  const pagesId = nextId++;
  const pageIds = pages.map(() => nextId++);
  const contentIds = pages.map(() => nextId++);
  const imageIds = list.map(() => nextId++);
  const infoId = nextId++;
  const offsets = new Array(infoId + 1);
  const chunks = [];
  let offset = 0;

  const push = (bytes) => {
    chunks.push(bytes);
    offset += bytes.length;
  };

  const writeObject = (id, parts) => {
    offsets[id] = offset;
    push(concat([ascii(`${id} 0 obj\n`), ...parts, ascii("endobj\n")]));
  };

  push(ascii("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"));

  writeObject(catalogId, [ascii(`<< /Type /Catalog /Pages ${pagesId} 0 R /PageLayout /OneColumn >>\n`)]);

  writeObject(pagesId, [ascii(
    `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageIds.length} >>\n`
  )]);

  pages.forEach((page, pageIndex) => {
    const used = [];
    const seen = new Set();
    for (const item of page.items) {
      const imageIndex = indexByShot.get(item.shot);
      if (seen.has(imageIndex)) continue;
      seen.add(imageIndex);
      used.push(`/Im${imageIndex} ${imageIds[imageIndex]} 0 R`);
    }
    writeObject(pageIds[pageIndex], [ascii(
      `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${fmt(PAGE_WIDTH)} ${fmt(page.height)}] /Contents ${contentIds[pageIndex]} 0 R /Resources << /ProcSet [/PDF /ImageC] /XObject << ${used.join(" ")} >> >> >>\n`
    )]);
  });

  pages.forEach((page, pageIndex) => {
    const stream = contentFor(page, indexByShot);
    writeObject(contentIds[pageIndex], [
      ascii(`<< /Length ${stream.length} >>\nstream\n`),
      ascii(stream),
      ascii("\nendstream\n")
    ]);
  });

  list.forEach((shot, imageIndex) => {
    writeObject(imageIds[imageIndex], [
      ascii(`<< /Type /XObject /Subtype /Image /Width ${shot.width} /Height ${shot.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${shot.bytes.length} >>\nstream\n`),
      shot.bytes,
      ascii("\nendstream\n")
    ]);
  });

  writeObject(infoId, [ascii("<< /Title (Livro de prints) /Producer (Livro de prints) >>\n")]);

  for (let id = 1; id < offsets.length; id += 1) {
    if (offsets[id] === undefined) throw new Error(`objeto ${id}`);
  }

  const startxref = offset;
  let xref = `xref\n0 ${offsets.length}\n`;
  xref += "0000000000 65535 f \n";
  for (let id = 1; id < offsets.length; id += 1) {
    xref += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  push(ascii(xref));
  push(ascii(`trailer\n<< /Size ${offsets.length} /Root ${catalogId} 0 R /Info ${infoId} 0 R >>\nstartxref\n${startxref}\n%%EOF\n`));
  return concat(chunks);
}

async function savePdf() {
  if (!shots.length || saving) return;
  saving = true;
  updateControls();
  setStatus("A gerar o PDF…");
  try {
    const bytes = buildPdf(shots);
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "livro-de-prints.pdf";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2500);
    setStatus("PDF baixado. A ordem é a mesma da pilha, de cima para baixo.");
  } catch (err) {
    setStatus("Não foi possível gerar o PDF.");
  } finally {
    saving = false;
    updateControls();
  }
}

shootBtn.addEventListener("click", () => {
  tirarPrint();
});
saveBtn.addEventListener("click", () => {
  savePdf();
});

if (!window.isSecureContext) {
  setStatus("Abra esta página em HTTPS para tirar um print.");
}
updateControls();
