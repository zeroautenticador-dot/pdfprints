"use strict";

const PAGE_WIDTH = 595.28;
const MAX_PAGE_HEIGHT = 14400;

const shareBtn = document.querySelector("#share");
const shootBtn = document.querySelector("#shoot");
const saveBtn = document.querySelector("#save");
const stackEl = document.querySelector("#stack");
const statusEl = document.querySelector("#status");
const countEl = document.querySelector("#count");
const preview = document.querySelector("#preview");
const liveLabel = document.querySelector("#live-label");

let stream = null;
let shots = [];
let seq = 1;
let shooting = false;
let saving = false;

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
  const live = isLive();
  document.body.classList.toggle("is-live", live);
  shareBtn.setAttribute("aria-pressed", live ? "true" : "false");
  shootBtn.disabled = !live || shooting;
  saveBtn.disabled = shots.length === 0 || saving;
  countEl.textContent = `Caderno · ${printsLabel(shots.length)}`;
  if (!live) {
    liveLabel.textContent = "Nenhuma tela compartilhada";
    return;
  }
  const width = preview.videoWidth;
  const height = preview.videoHeight;
  liveLabel.textContent = width && height ? `Tela ativa · ${width}×${height}` : "Tela ativa";
}

function render({ scroll = false } = {}) {
  updateControls();
  if (shots.length === 0) {
    stackEl.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nenhum print ainda. Use Compartilhar tela e depois Tirar print.";
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
  if (preview.srcObject === current) preview.srcObject = null;
}

function attachStream(next) {
  const previous = stream;
  stream = next;
  if (previous) {
    for (const track of previous.getTracks()) track.stop();
  }
  preview.srcObject = next;
  const finish = () => {
    if (stream !== next) return;
    if (next.getVideoTracks().some((item) => item.readyState === "live")) return;
    releaseStream();
    updateControls();
    setStatus("Compartilhamento encerrado.");
  };
  next.addEventListener("inactive", finish);
  const track = next.getVideoTracks()[0];
  if (track) track.addEventListener("ended", finish);
  preview.play().catch(() => {});
  updateControls();
}

async function share() {
  if (!window.isSecureContext) {
    setStatus("Abra esta página em HTTPS para compartilhar a tela.");
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    setStatus("Este navegador não compartilha a tela.");
    return;
  }
  shareBtn.disabled = true;
  try {
    const next = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false
    });
    attachStream(next);
    setStatus("Tela compartilhada. Tire um print quando quiser.");
  } catch (err) {
    const name = err && err.name;
    if (name === "NotAllowedError" || name === "AbortError") {
      setStatus(isLive() ? "A tela anterior continua compartilhada." : "Compartilhamento cancelado.");
    } else {
      setStatus("Não foi possível compartilhar a tela.");
    }
  } finally {
    shareBtn.disabled = false;
    updateControls();
  }
}

async function shoot() {
  if (shooting || !isLive()) {
    if (!isLive()) setStatus("Compartilhe a tela antes de tirar um print.");
    return;
  }
  const width = preview.videoWidth;
  const height = preview.videoHeight;
  if (!width || !height) {
    setStatus("A imagem da tela ainda não chegou. Tente de novo.");
    return;
  }
  shooting = true;
  updateControls();
  setStatus("A capturar…");
  try {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d", { alpha: false });
    ctx.drawImage(preview, 0, 0, width, height);
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
    shots.push({ id: seq++, url, width, height, bytes });
    render({ scroll: true });
    setStatus(`Print ${shots.length} guardado no fim da pilha.`);
  } catch (err) {
    setStatus("Não foi possível guardar este print.");
  } finally {
    shooting = false;
    updateControls();
  }
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

shareBtn.addEventListener("click", () => {
  share();
});
shootBtn.addEventListener("click", () => {
  shoot();
});
saveBtn.addEventListener("click", () => {
  savePdf();
});
preview.addEventListener("resize", updateControls);

if (!window.isSecureContext) {
  setStatus("Abra esta página em HTTPS para compartilhar a tela.");
}
updateControls();
