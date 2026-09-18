/*
 * Data & Analytics News: client-side fetch + render.
 *
 * Talks only to our own /api/news (no external key, no external
 * call from the browser). Runs after the page is already usable,
 * the upload UI never waits on this. Any failure here just shows a
 * quiet "temporarily unavailable" message, it never breaks the rest
 * of Omixa.
 */

function newsCardHtml(article) {
  const img = article.image || '/static/img/news-fallback.svg';
  const meta = [article.source, article.published_display].filter(Boolean).join(' · ');
  return `
    <article class="news-card">
      <img class="news-card-img" src="${escapeAttr(img)}" alt="" loading="lazy"
           onerror="this.onerror=null;this.src='/static/img/news-fallback.svg';">
      <div class="news-card-body">
        <h3>${escapeHtml(article.title)}</h3>
        ${article.description ? `<p>${escapeHtml(article.description)}</p>` : ''}
        <div class="news-meta">${escapeHtml(meta)}</div>
        <a class="news-read-link" href="${escapeAttr(article.url)}" target="_blank" rel="noopener noreferrer">Read article &rarr;</a>
      </div>
    </article>`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, '&quot;');
}

/** Homepage carousel: fetch once, render cards, wire up prev/next
 * scroll buttons. Degrades to a plain "unavailable" message on any
 * failure or empty result. */
function initNewsCarousel(carouselId, skeletonId, prevId, nextId) {
  const carousel = document.getElementById(carouselId);
  const prevBtn = document.getElementById(prevId);
  const nextBtn = document.getElementById(nextId);
  if (!carousel) return;

  const scrollByCard = (dir) => {
    const card = carousel.querySelector('.news-card');
    const amount = card ? card.getBoundingClientRect().width + 18 : 280;
    carousel.scrollBy({ left: dir * amount, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  };
  if (prevBtn) prevBtn.addEventListener('click', () => scrollByCard(-1));
  if (nextBtn) nextBtn.addEventListener('click', () => scrollByCard(1));

  fetch('/api/news?category=all')
    .then((res) => res.json())
    .then((data) => {
      if (data.status !== 'ok' || !data.articles || data.articles.length === 0) {
        renderNewsState(carousel, 'News is temporarily unavailable.');
        return;
      }
      carousel.innerHTML = data.articles.map(newsCardHtml).join('');
    })
    .catch(() => {
      renderNewsState(carousel, 'News is temporarily unavailable.');
    });
}

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function renderNewsState(container, message) {
  container.innerHTML = `<div class="news-state">${escapeHtml(message)}</div>`;
}

/** /news page: filter tabs + grid. */
function initNewsPage(gridId, filtersSelector) {
  const grid = document.getElementById(gridId);
  const buttons = Array.from(document.querySelectorAll(filtersSelector));
  if (!grid) return;

  function load(category) {
    grid.innerHTML = '<div class="news-state">Loading articles…</div>';
    fetch(`/api/news?category=${encodeURIComponent(category)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.status !== 'ok') {
          renderNewsState(grid, 'News is temporarily unavailable.');
          return;
        }
        if (!data.articles || data.articles.length === 0) {
          renderNewsState(grid, 'No articles in this category right now.');
          return;
        }
        grid.innerHTML = data.articles.map(newsCardHtml).join('');
      })
      .catch(() => renderNewsState(grid, 'News is temporarily unavailable.'));
  }

  buttons.forEach((btn) => {
    btn.addEventListener('click', () => {
      buttons.forEach((b) => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      load(btn.dataset.category);
    });
  });

  const initial = buttons.find((b) => b.classList.contains('active'));
  load(initial ? initial.dataset.category : 'all');
}
