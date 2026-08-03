// Shared gallery + lightbox for run/case figure previews. Used by both the
// run page (a single run's plots) and the figures page (a case's first-run
// preview), so the lightbox overlay is built once, lazily, and appended to
// <body> rather than duplicated as markup in every page that needs one.

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);

const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

let lightboxEl = null;
let lightboxImgEl = null;
let lightboxCaptionEl = null;
let lightboxPrevEl = null;
let lightboxNextEl = null;
let lightboxImages = [];
let lightboxIndex = 0;

function showLightboxImage(index) {
  if (!lightboxImages.length) return;
  lightboxIndex = (index + lightboxImages.length) % lightboxImages.length;
  const item = lightboxImages[lightboxIndex];
  lightboxImgEl.src = item.url;
  lightboxImgEl.alt = item.name;
  lightboxCaptionEl.textContent =
    lightboxImages.length > 1
      ? `${item.name} (${lightboxIndex + 1} / ${lightboxImages.length})`
      : item.name;
  const showNav = lightboxImages.length > 1;
  lightboxPrevEl.hidden = !showNav;
  lightboxNextEl.hidden = !showNav;
}

function closeLightbox() {
  if (!lightboxEl) return;
  lightboxEl.hidden = true;
  lightboxImgEl.src = "";
  document.body.style.overflow = "";
}

function ensureLightbox() {
  if (lightboxEl) return;

  lightboxEl = document.createElement("div");
  lightboxEl.className = "lightbox";
  lightboxEl.hidden = true;
  lightboxEl.innerHTML = `
    <button class="lightbox-close" type="button" aria-label="Close">✕</button>
    <button class="lightbox-nav lightbox-prev" type="button" aria-label="Previous image">‹</button>
    <img class="lightbox-img" alt="" />
    <button class="lightbox-nav lightbox-next" type="button" aria-label="Next image">›</button>
    <p class="lightbox-caption"></p>
  `;
  document.body.appendChild(lightboxEl);

  lightboxImgEl = lightboxEl.querySelector(".lightbox-img");
  lightboxCaptionEl = lightboxEl.querySelector(".lightbox-caption");
  lightboxPrevEl = lightboxEl.querySelector(".lightbox-prev");
  lightboxNextEl = lightboxEl.querySelector(".lightbox-next");

  lightboxEl.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
  lightboxPrevEl.addEventListener("click", () => showLightboxImage(lightboxIndex - 1));
  lightboxNextEl.addEventListener("click", () => showLightboxImage(lightboxIndex + 1));
  lightboxEl.addEventListener("click", (event) => {
    if (event.target === lightboxEl) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (lightboxEl.hidden) return;
    if (event.key === "Escape") closeLightbox();
    else if (event.key === "ArrowLeft") showLightboxImage(lightboxIndex - 1);
    else if (event.key === "ArrowRight") showLightboxImage(lightboxIndex + 1);
  });
}

function openLightbox(images, index) {
  ensureLightbox();
  lightboxImages = images;
  showLightboxImage(index);
  lightboxEl.hidden = false;
  document.body.style.overflow = "hidden";
  lightboxEl.querySelector(".lightbox-close").focus();
}

/**
 * Render a gallery grid of image/PDF paths (relative to the figures output
 * root) into `container`. Images open in a shared in-page lightbox; PDFs
 * open in a new tab. Returns true if anything was rendered.
 */
export function renderGallery(container, gallery, baseUrl) {
  if (!Array.isArray(gallery) || !gallery.length) {
    container.innerHTML = "";
    return false;
  }

  const items = gallery.map((path) => {
    const url = `${baseUrl}figures/${path}`;
    const name = path.split("/").pop() ?? path;
    const ext = name.split(".").pop()?.toLowerCase() ?? "";
    return { url, name, isImage: IMAGE_EXTENSIONS.has(ext) };
  });

  const images = items.filter((item) => item.isImage);

  let imageIndex = 0;
  container.innerHTML = items
    .map((item) => {
      if (item.isImage) {
        const index = imageIndex;
        imageIndex += 1;
        return `
          <button class="gallery-item" type="button" data-lightbox-index="${index}">
            <img src="${esc(item.url)}" alt="${esc(item.name)}" loading="lazy" />
            <span class="gallery-caption">${esc(item.name)}</span>
          </button>
        `;
      }
      return `
        <a class="gallery-item" href="${esc(item.url)}" target="_blank" rel="noopener">
          <embed src="${esc(item.url)}" type="application/pdf" style="width: 100%; height: 320px; border-radius: 6px;" />
          <span class="gallery-caption">${esc(item.name)}</span>
        </a>
      `;
    })
    .join("");

  if (images.length) {
    container.querySelectorAll("[data-lightbox-index]").forEach((el) => {
      el.addEventListener("click", () => openLightbox(images, Number(el.dataset.lightboxIndex)));
    });
  }

  return true;
}
