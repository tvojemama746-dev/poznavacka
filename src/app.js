const DATA_URL = "./data/plant-images.json";
const STORAGE_KEY = "biologiePoznavackaProgressV1";
const SETTINGS_KEY = "biologiePoznavackaSettingsV1";
const SESSION_LENGTH = 10;
const DEFAULT_ANSWER_COUNT = 4;
const MIN_ANSWER_COUNT = 4;
const MAX_ANSWER_COUNT = 12;

const REGIONS = [
  { id: "all", label: "Všechny oblasti", range: [1, Infinity] },
  { id: "1-40", label: "1-40", range: [1, 40] },
  { id: "41-80", label: "41-80", range: [41, 80] },
  { id: "81-120", label: "81-120", range: [81, 120] },
  { id: "121-zaver", label: "121-závěr", range: [121, Infinity] },
];

const app = document.querySelector("#app");

const state = {
  plants: [],
  trainablePlants: [],
  selectedRegion: "all",
  view: "home",
  progress: loadProgress(),
  settings: loadSettings(),
  session: null,
};

registerServiceWorker();
init();

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => {
      // Instalace jako PWA je bonus; samotný trénink musí fungovat i bez ní.
    });
  });
}

async function init() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const plants = await response.json();
    state.plants = plants.map(normalizePlant);
    state.trainablePlants = state.plants.filter((plant) => plant.images.length > 0);
    renderHome();
  } catch (error) {
    app.innerHTML = `
      <main class="fatal">
        <section>
          <h1>Data se nepodařilo načíst</h1>
          <p>Spusť aplikaci přes statický server, například <code>python3 -m http.server</code>.</p>
          <p>${escapeHtml(error.message)}</p>
        </section>
      </main>
    `;
  }
}

function normalizePlant(plant) {
  return {
    ...plant,
    latin: Array.isArray(plant.latin) ? plant.latin : [],
    note: plant.note || "",
    regionId: getRegionForNumber(plant.number).id,
    images: Array.isArray(plant.images)
      ? plant.images
          .filter((image) => image && image.local_path)
          .map((image) => ({
            ...image,
            local_path: normalizeImagePath(image.local_path),
          }))
      : [],
  };
}

function normalizeImagePath(path) {
  if (/^(https?:)?\/\//.test(path) || path.startsWith("./")) {
    return path;
  }
  return `./${path.replace(/^\/+/, "")}`;
}

function getRegionForNumber(number) {
  return REGIONS.find((region) => number >= region.range[0] && number <= region.range[1] && region.id !== "all") || REGIONS[4];
}

function getRegionPlants(regionId, trainableOnly = true) {
  const source = trainableOnly ? state.trainablePlants : state.plants;
  if (regionId === "all") {
    return source;
  }
  return source.filter((plant) => plant.regionId === regionId);
}

function loadProgress() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveProgress() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.progress));
  } catch {
    // Trénink zůstane použitelný i v prohlížeči, který lokální úložiště blokuje.
  }
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      return getDefaultSettings();
    }
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return getDefaultSettings();
  }
}

function getDefaultSettings() {
  return {
    answerCount: DEFAULT_ANSWER_COUNT,
  };
}

function normalizeSettings(settings) {
  const answerCount = Number(settings?.answerCount);
  return {
    answerCount: clampAnswerCount(Number.isFinite(answerCount) ? answerCount : DEFAULT_ANSWER_COUNT),
  };
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  } catch {
    // Nastavení je pohodlnost navíc; aplikace má fungovat i bez lokálního úložiště.
  }
}

function clampAnswerCount(value) {
  return Math.min(MAX_ANSWER_COUNT, Math.max(MIN_ANSWER_COUNT, Math.round(value)));
}

function getRecord(slug) {
  if (!state.progress[slug]) {
    state.progress[slug] = {
      correct: 0,
      wrong: 0,
      lastResult: null,
      lastPracticedAt: null,
      mastery: 0,
    };
  }
  return state.progress[slug];
}

function getExistingRecord(slug) {
  return state.progress[slug] || {
    correct: 0,
    wrong: 0,
    lastResult: null,
    lastPracticedAt: null,
    mastery: 0,
  };
}

function updateRecord(plant, isCorrect) {
  const record = getRecord(plant.slug);
  if (isCorrect) {
    record.correct += 1;
    record.mastery = Math.min(5, record.mastery + 1);
  } else {
    record.wrong += 1;
    record.mastery = Math.max(0, record.mastery - 1);
  }
  record.lastResult = isCorrect;
  record.lastPracticedAt = Date.now();
  saveProgress();
}

function getStats(regionId = "all") {
  const plants = getRegionPlants(regionId);
  const allPlants = getRegionPlants(regionId, false);
  const mastered = plants.filter((plant) => getExistingRecord(plant.slug).mastery >= 5).length;
  const review = plants.filter(isReviewPlant).length;
  const attempts = plants.reduce((sum, plant) => {
    const record = getExistingRecord(plant.slug);
    return sum + record.correct + record.wrong;
  }, 0);
  return {
    total: allPlants.length,
    trainable: plants.length,
    mastered,
    review,
    attempts,
    percent: plants.length ? Math.round((mastered / plants.length) * 100) : 0,
  };
}

function isReviewPlant(plant) {
  const record = getExistingRecord(plant.slug);
  return record.wrong > 0 && record.mastery < 5;
}

function renderHome() {
  state.view = "home";
  state.session = null;
  const stats = getStats(state.selectedRegion);
  const selectedRegion = REGIONS.find((region) => region.id === state.selectedRegion);
  const canReview = getRegionPlants(state.selectedRegion).some(isReviewPlant);

  app.innerHTML = `
    <main class="screen">
      <section class="hero">
        <div>
          <p class="eyebrow">Poznávačka rostlin</p>
          <h1>Trénuj poznávání podle fotek.</h1>
          <p class="lead">Krátké série po deseti otázkách, české názvy jako hlavní odpověď a latinský rod po vyhodnocení.</p>
        </div>
        <div class="panel">
          <div class="stats-grid">
            ${renderStat(stats.trainable, "rostlin k tréninku")}
            ${renderStat(`${stats.percent}%`, "zvládnuto")}
            ${renderStat(stats.review, "k opakování")}
            ${renderStat(stats.attempts, "odpovědí")}
          </div>
        </div>
      </section>

      <section class="home-grid">
        <div class="panel">
          <h2>Oblast</h2>
          <div class="region-list">
            ${REGIONS.map(renderRegionButton).join("")}
          </div>
        </div>

        <div class="panel">
          <h2>${escapeHtml(selectedRegion.label)}</h2>
          <p class="lead">${stats.trainable} z ${stats.total} rostlin má obrázky. Série vybírá častěji nové a chybované rostliny.</p>
          <div class="actions">
            <button class="button primary" data-action="start-training">Trénovat</button>
            <button class="button warning" data-action="start-review" ${canReview ? "" : "disabled"}>Opakovat chyby</button>
            <button class="button" data-action="overview">Přehled</button>
            <button class="button" data-action="settings">Nastavení</button>
          </div>
          ${canReview ? "" : '<p class="notice">V této oblasti zatím nejsou žádné rostliny k opakování.</p>'}
        </div>
      </section>
    </main>
  `;

  bindHomeEvents();
}

function renderStat(value, label) {
  return `
    <div class="stat">
      <strong>${escapeHtml(String(value))}</strong>
      <span>${escapeHtml(label)}</span>
    </div>
  `;
}

function renderRegionButton(region) {
  const stats = getStats(region.id);
  const active = region.id === state.selectedRegion ? " active" : "";
  return `
    <button class="region-button${active}" data-region="${escapeHtml(region.id)}">
      <span>
        <span class="region-name">${escapeHtml(region.label)}</span>
        <span class="region-meta">${stats.trainable}/${stats.total} rostlin, ${stats.review} k opakování</span>
      </span>
      <span class="mastery-pill">${stats.percent}%</span>
    </button>
  `;
}

function bindHomeEvents() {
  app.querySelectorAll("[data-region]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedRegion = button.dataset.region;
      renderHome();
    });
  });
  app.querySelector('[data-action="start-training"]').addEventListener("click", () => startSession("training"));
  app.querySelector('[data-action="overview"]').addEventListener("click", renderOverview);
  app.querySelector('[data-action="settings"]').addEventListener("click", renderSettings);
  const reviewButton = app.querySelector('[data-action="start-review"]');
  if (reviewButton) {
    reviewButton.addEventListener("click", () => startSession("review"));
  }
}

function startSession(mode) {
  const basePool = getRegionPlants(state.selectedRegion);
  const pool = mode === "review" ? basePool.filter(isReviewPlant) : basePool;
  if (!pool.length) {
    renderHome();
    return;
  }
  state.session = {
    mode,
    pool,
    current: null,
    index: 0,
    score: 0,
    answered: false,
    selectedAnswer: null,
    seenSlugs: new Set(),
    brokenImages: new Set(),
  };
  nextQuestion();
}

function nextQuestion() {
  const session = state.session;
  if (!session) {
    renderHome();
    return;
  }
  if (session.index >= SESSION_LENGTH) {
    renderSummary();
    return;
  }

  const plant = pickWeightedPlant(session);
  const image = pickPlantImage(plant, session.brokenImages);
  if (!image) {
    session.seenSlugs.add(plant.slug);
    if (session.seenSlugs.size >= session.pool.length) {
      renderSummary();
      return;
    }
    nextQuestion();
    return;
  }

  session.current = {
    plant,
    image,
    options: buildOptions(plant),
  };
  session.index += 1;
  session.answered = false;
  session.selectedAnswer = null;
  session.seenSlugs.add(plant.slug);
  renderQuiz();
}

function pickWeightedPlant(session) {
  const unseen = session.pool.filter((plant) => !session.seenSlugs.has(plant.slug));
  const candidates = unseen.length ? unseen : session.pool;
  if (!unseen.length) {
    session.seenSlugs.clear();
  }
  const weighted = candidates.map((plant) => {
    const record = getExistingRecord(plant.slug);
    let weight = 6 - record.mastery;
    if (record.correct + record.wrong === 0) {
      weight += 3;
    }
    if (record.wrong > 0 && record.mastery < 5) {
      weight += 3;
    }
    if (record.lastResult === false) {
      weight += 2;
    }
    return { plant, weight: Math.max(1, weight) };
  });
  const total = weighted.reduce((sum, item) => sum + item.weight, 0);
  let roll = Math.random() * total;
  for (const item of weighted) {
    roll -= item.weight;
    if (roll <= 0) {
      return item.plant;
    }
  }
  return weighted[weighted.length - 1].plant;
}

function pickPlantImage(plant, brokenImages) {
  const available = plant.images.filter((image) => !brokenImages.has(image.local_path));
  if (!available.length) {
    return null;
  }
  return available[Math.floor(Math.random() * available.length)];
}

function buildOptions(correctPlant) {
  const answerCount = Math.min(state.settings.answerCount, state.trainablePlants.length);
  const sameRegion = state.trainablePlants.filter(
    (plant) => plant.slug !== correctPlant.slug && plant.regionId === correctPlant.regionId,
  );
  const fallback = state.trainablePlants.filter(
    (plant) => plant.slug !== correctPlant.slug && plant.regionId !== correctPlant.regionId,
  );
  const preferred = shuffle([...sameRegion]);
  const backup = shuffle([...fallback]);
  const selected = [correctPlant];

  for (const plant of preferred) {
    if (selected.length >= answerCount) {
      break;
    }
    if (!selected.some((item) => item.czech === plant.czech)) {
      selected.push(plant);
    }
  }

  for (const plant of backup) {
    if (selected.length >= answerCount) {
      break;
    }
    if (!selected.some((item) => item.czech === plant.czech)) {
      selected.push(plant);
    }
  }

  return shuffle(selected).map((plant) => ({
    slug: plant.slug,
    label: plant.czech,
    correct: plant.slug === correctPlant.slug,
  }));
}

function renderQuiz() {
  const session = state.session;
  const question = session.current;
  const plant = question.plant;
  const progressPercent = Math.round(((session.index - 1) / SESSION_LENGTH) * 100);
  const title = session.mode === "review" ? "Opakování chyb" : "Trénink";

  app.innerHTML = `
    <main class="screen">
      <section class="quiz-header">
        <div class="toolbar">
          <button class="button" data-action="home">Domů</button>
          <span class="badge">${escapeHtml(title)}</span>
          <span class="badge">${escapeHtml(getRegionForNumber(plant.number).label)}</span>
        </div>
        <div class="progress-row">
          <div class="progress-track" aria-hidden="true">
            <div class="progress-fill" style="width: ${progressPercent}%"></div>
          </div>
          <span>${session.index}/${SESSION_LENGTH}</span>
        </div>
      </section>

      <section class="quiz-layout">
        <article class="quiz-card">
          <div class="image-wrap">
            <img src="${escapeAttribute(question.image.local_path)}" alt="Rostlina k určení">
          </div>
          <div class="question-body">
            <div class="prompt-row">
              <p>Co je to za rostlinu?</p>
              <span class="badge">${session.score} správně</span>
            </div>
            <div class="answer-grid">
              ${question.options.map(renderAnswerButton).join("")}
            </div>
            <div data-feedback></div>
          </div>
        </article>
      </section>
    </main>
  `;

  const image = app.querySelector(".image-wrap img");
  image.addEventListener("error", () => {
    session.brokenImages.add(question.image.local_path);
    session.index -= 1;
    nextQuestion();
  }, { once: true });

  app.querySelector('[data-action="home"]').addEventListener("click", renderHome);
  app.querySelectorAll("[data-answer]").forEach((button) => {
    button.addEventListener("click", () => answerQuestion(button.dataset.answer));
  });
}

function renderAnswerButton(option) {
  return `
    <button class="answer-button" data-answer="${escapeAttribute(option.slug)}">
      ${escapeHtml(option.label)}
    </button>
  `;
}

function answerQuestion(slug) {
  const session = state.session;
  if (!session || session.answered) {
    return;
  }
  const question = session.current;
  const isCorrect = slug === question.plant.slug;
  session.answered = true;
  session.selectedAnswer = slug;
  if (isCorrect) {
    session.score += 1;
  }
  updateRecord(question.plant, isCorrect);

  app.querySelectorAll("[data-answer]").forEach((button) => {
    const optionSlug = button.dataset.answer;
    button.disabled = true;
    if (optionSlug === question.plant.slug) {
      button.classList.add("correct");
    }
    if (optionSlug === slug && !isCorrect) {
      button.classList.add("incorrect");
    }
  });

  const feedback = app.querySelector("[data-feedback]");
  feedback.innerHTML = `
    <div class="feedback ${isCorrect ? "good" : "bad"}">
      <p><strong>${isCorrect ? "Správně." : "Správně je:"} ${escapeHtml(question.plant.czech)}</strong></p>
      <p class="latin">${escapeHtml(question.plant.latin.join(" / ") || "latinský název neuveden")}</p>
      ${question.plant.note ? `<p>${escapeHtml(question.plant.note)}</p>` : ""}
      <button class="button primary" data-action="next">${session.index >= SESSION_LENGTH ? "Vyhodnotit" : "Další"}</button>
    </div>
  `;
  feedback.querySelector('[data-action="next"]').addEventListener("click", nextQuestion);
}

function renderSummary() {
  const session = state.session;
  const reviewCount = getRegionPlants(state.selectedRegion).filter(isReviewPlant).length;
  app.innerHTML = `
    <main class="screen">
      <section class="panel summary">
        <p class="eyebrow">${session.mode === "review" ? "Opakování dokončeno" : "Série dokončena"}</p>
        <h1>Výsledek</h1>
        <div class="summary-score">${session.score}/${SESSION_LENGTH}</div>
        <p class="lead">Progres je uložený jen v tomto prohlížeči.</p>
        <div class="actions">
          <button class="button primary" data-action="again">Další série</button>
          <button class="button warning" data-action="review" ${reviewCount ? "" : "disabled"}>Opakovat chyby</button>
          <button class="button" data-action="home">Domů</button>
        </div>
      </section>
    </main>
  `;
  app.querySelector('[data-action="again"]').addEventListener("click", () => startSession("training"));
  app.querySelector('[data-action="home"]').addEventListener("click", renderHome);
  const reviewButton = app.querySelector('[data-action="review"]');
  if (reviewButton) {
    reviewButton.addEventListener("click", () => startSession("review"));
  }
}

function renderSettings() {
  state.view = "settings";
  state.session = null;
  const answerCount = state.settings.answerCount;

  app.innerHTML = `
    <main class="screen">
      <section class="hero">
        <div>
          <p class="eyebrow">Nastavení</p>
          <h1>Uprav si obtížnost tréninku.</h1>
          <p class="lead">Počet možností se použije v nově spuštěné sérii otázek.</p>
        </div>
        <div class="panel">
          <div class="toolbar">
            <button class="button" data-action="home">Domů</button>
          </div>
        </div>
      </section>

      <section class="settings-layout">
        <article class="panel setting-panel">
          <div class="setting-heading">
            <div>
              <h2>Počet možných odpovědí</h2>
              <p class="lead">Vyber hodnotu od ${MIN_ANSWER_COUNT} do ${MAX_ANSWER_COUNT}.</p>
            </div>
            <output class="setting-value" for="answerCount">${answerCount}</output>
          </div>
          <input
            id="answerCount"
            class="range-input"
            type="range"
            min="${MIN_ANSWER_COUNT}"
            max="${MAX_ANSWER_COUNT}"
            step="1"
            value="${answerCount}"
          >
          <div class="range-labels" aria-hidden="true">
            <span>${MIN_ANSWER_COUNT}</span>
            <span>${MAX_ANSWER_COUNT}</span>
          </div>
        </article>
      </section>
    </main>
  `;

  app.querySelector('[data-action="home"]').addEventListener("click", renderHome);
  const answerCountInput = app.querySelector("#answerCount");
  const answerCountOutput = app.querySelector(".setting-value");
  answerCountInput.addEventListener("input", () => {
    const nextAnswerCount = clampAnswerCount(Number(answerCountInput.value));
    state.settings.answerCount = nextAnswerCount;
    answerCountOutput.textContent = nextAnswerCount;
    saveSettings();
  });
}

function renderOverview() {
  state.view = "overview";
  app.innerHTML = `
    <main class="screen">
      <section class="hero">
        <div>
          <p class="eyebrow">Přehled</p>
          <h1>Rostliny a lokální progres.</h1>
          <p class="lead">Přehled ukazuje počet obrázků, zvládnutí a rostliny, které ještě čekají na opravu.</p>
        </div>
        <div class="panel">
          <div class="toolbar">
            <button class="button" data-action="home">Domů</button>
            <button class="button danger" data-action="reset">Reset progresu</button>
          </div>
        </div>
      </section>
      <section class="overview">
        ${REGIONS.filter((region) => region.id !== "all").map(renderOverviewRegion).join("")}
      </section>
    </main>
  `;

  app.querySelector('[data-action="home"]').addEventListener("click", renderHome);
  app.querySelector('[data-action="reset"]').addEventListener("click", () => {
    const confirmed = window.confirm("Opravdu smazat lokální progres v tomto prohlížeči?");
    if (!confirmed) {
      return;
    }
    state.progress = {};
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Viz saveProgress: reset nesmí rozbít samotnou aplikaci.
    }
    renderOverview();
  });
}

function renderOverviewRegion(region) {
  const plants = getRegionPlants(region.id, false);
  const stats = getStats(region.id);
  return `
    <article class="panel">
      <h2>${escapeHtml(region.label)}</h2>
      <p class="lead">${stats.trainable}/${stats.total} rostlin s obrázky, ${stats.percent}% zvládnuto.</p>
      <div class="plant-list">
        ${plants.map(renderPlantRow).join("")}
      </div>
    </article>
  `;
}

function renderPlantRow(plant) {
  const record = getExistingRecord(plant.slug);
  const trainable = plant.images.length > 0;
  const latin = plant.latin.join(" / ");
  const status = !trainable
    ? '<span class="badge">bez obrázku</span>'
    : record.wrong > 0 && record.mastery < 5
      ? '<span class="badge error">opakovat</span>'
      : '<span class="badge ready">trénovat</span>';
  return `
    <div class="plant-row">
      <div class="plant-title">
        <strong>${plant.number}. ${escapeHtml(plant.czech)}</strong>
        <span>${escapeHtml(latin)}</span>
      </div>
      <div class="badges">
        <span class="badge">${plant.images.length} obr.</span>
        <span class="badge">${record.mastery}/5</span>
        ${status}
      </div>
    </div>
  `;
}

function shuffle(items) {
  const copy = [...items];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}
