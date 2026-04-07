"""Blueprint Enterprise Design System CSS for the Stitch UI."""

from __future__ import annotations

STUDIO_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@300;400;500;600&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap');

:root {
    /* Primary palette */
    --primary: #4343d5;
    --primary-container: #5d5fef;
    --on-primary: #ffffff;
    --on-primary-container: #faf7ff;
    --primary-fixed: #e1e0ff;
    --primary-fixed-dim: #c1c1ff;

    /* Surfaces — tonal layering */
    --surface: #faf9ff;
    --surface-container-lowest: #ffffff;
    --surface-container-low: #f2f3fd;
    --surface-container: #ededf7;
    --surface-container-high: #e7e7f1;
    --surface-container-highest: #e1e2eb;
    --surface-dim: #d9d9e3;
    --surface-variant: #e1e2eb;
    --on-surface: #191b22;
    --on-surface-variant: #464555;

    /* Outline */
    --outline: #767586;
    --outline-variant: #c7c4d7;
    --ghost-border: rgba(199, 196, 215, 0.2);

    /* Semantic */
    --error: #ba1a1a;
    --error-container: #ffdad6;
    --on-error-container: #93000a;
    --tertiary: #904400;
    --tertiary-container: #b65700;
    --tertiary-fixed: #ffdbc8;
    --secondary: #575995;
    --secondary-container: #babbfe;
    --on-secondary-container: #474984;

    /* Functional */
    --success: #28A464;
    --warning: #904400;
    --info: #4343d5;

    /* Typography */
    --font-headline: 'Manrope', sans-serif;
    --font-body: 'Inter', sans-serif;

    /* Ambient shadows */
    --shadow-sm: 0 4px 24px rgba(25, 27, 34, 0.04);
    --shadow-md: 0 8px 32px rgba(25, 27, 34, 0.06);
    --shadow-lg: 0 12px 40px rgba(25, 27, 34, 0.08);
    --shadow-float: 0 24px 40px rgba(25, 27, 34, 0.08);
}

/* ── Reset & Base ─────────────────────────────────────────────────── */
html, body {
    margin: 0;
    padding: 0;
    min-height: 100vh;
    background: var(--surface);
    color: var(--on-surface);
    font-family: var(--font-body);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
}

*, *::before, *::after { box-sizing: border-box; }

h1, h2, h3, h4 {
    font-family: var(--font-headline);
    color: var(--on-surface);
    margin: 0;
    font-weight: 700;
}

a {
    color: var(--primary);
    text-decoration: none;
    transition: color 0.2s ease;
}
a:hover { color: var(--primary-container); }

/* Material icon baseline */
.material-symbols-outlined {
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
    vertical-align: middle;
}

/* ── Header ───────────────────────────────────────────────────────── */
.domino-header {
    background: var(--surface);
    width: 100%;
    padding: 2rem 3rem 0.5rem;
}
.domino-header-inner {
    max-width: 1440px;
    margin: 0 auto;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
}
.domino-header-title {
    color: var(--on-surface);
    font-family: var(--font-headline);
    font-size: 2rem;
    font-weight: 800;
    letter-spacing: -0.02em;
}
.domino-header-subtitle {
    font-family: var(--font-body);
    font-size: 0.8125rem;
    font-weight: 500;
    color: var(--outline);
}
.domino-header-version {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--primary-fixed-dim);
    background: rgba(193, 193, 255, 0.12);
    padding: 2px 8px;
    border-radius: 2px;
}

/* ── Page Layout ──────────────────────────────────────────────────── */
.page {
    max-width: 1440px;
    margin: 0 auto;
    padding: 0.5rem 3rem 6rem;
    width: 100%;
    min-height: calc(100vh - 100px);
}
.hero {
    padding: 0.25rem 0 1rem 0;
}
.hero-tagline {
    font-family: var(--font-body);
    font-size: 0.9375rem;
    font-weight: 400;
    color: var(--on-surface-variant);
    margin: 0;
    line-height: 1.5;
}

#project-id-resolved {
    font-size: 0.78rem;
    color: var(--outline);
    padding: 0.25rem 0 0 0.15rem;
}
#project-id-resolved.resolved {
    color: var(--primary);
    font-weight: 500;
}
#project-id-resolved.error {
    color: var(--error);
}

/* ── 3-Column Grid ────────────────────────────────────────────────── */
.studio-grid {
    display: grid;
    grid-template-columns: 3fr 5fr 4fr;
    gap: 1.5rem;
    align-items: start;
}
@media (max-width: 1200px) {
    .studio-grid {
        grid-template-columns: 1fr 1fr;
    }
    .studio-grid .studio-col-right {
        grid-column: 1 / -1;
    }
}
@media (max-width: 800px) {
    .studio-grid {
        grid-template-columns: 1fr;
    }
}

/* ── Column Headers ───────────────────────────────────────────────── */
.col-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    padding: 0 0.125rem;
}
.col-header h2 {
    font-family: var(--font-headline);
    font-size: 1.125rem;
    font-weight: 700;
    color: var(--on-surface);
    letter-spacing: -0.02em;
}
.step-badge {
    font-family: var(--font-body);
    font-size: 0.6875rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--outline);
    order: -1;
}

/* ── Cards (No-Line Rule: tonal layering, not borders) ───────────── */
.bp-card {
    background: var(--surface-container-lowest);
    border-radius: 12px;
    padding: 1.5rem;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease;
}

/* ── Form Fields ──────────────────────────────────────────────────── */
.field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin-bottom: 1rem;
}
.field:last-child {
    margin-bottom: 0;
}
.field label {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--on-surface-variant);
}
.field input[type="text"],
.field input[type="number"],
.field input[type="password"],
.field select {
    background: var(--surface-container-low);
    border: 1px solid var(--ghost-border);
    border-radius: 8px;
    padding: 0.625rem 0.875rem;
    color: var(--on-surface);
    font-family: var(--font-body);
    font-size: 0.8125rem;
    font-weight: 500;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
    outline: none;
    min-width: 0;
}
.field input:focus,
.field select:focus {
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(67, 67, 213, 0.12);
}
.field input::placeholder {
    color: var(--outline);
    font-weight: 400;
}
.field select {
    cursor: pointer;
}
.field-hint-text {
    display: block;
    font-size: 11px;
    color: var(--outline);
    margin-top: 0.15rem;
}

/* Code root combo */
.code-root-wrap {
    display: flex;
    border: 1px solid transparent;
    border-radius: 8px;
    overflow: hidden;
    background: var(--surface-container-low);
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.code-root-wrap:focus-within {
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(67, 67, 213, 0.12);
}
.code-root-prefix {
    padding: 0.625rem 0.875rem;
    background: var(--surface-container);
    border: none;
    border-right: 1px solid var(--ghost-border);
    font-size: 0.8125rem;
    color: var(--primary);
    font-family: ui-monospace, monospace;
    white-space: nowrap;
    user-select: none;
}
.code-root-suffix {
    flex: 1;
    border: none;
    padding: 0.625rem 0.875rem;
    font-size: 0.8125rem;
    color: var(--on-surface);
    background: transparent;
    outline: none;
    min-width: 0;
    font-family: ui-monospace, monospace;
}
.code-root-suffix::placeholder { color: var(--outline); }

/* Label row with info tooltip */
.label-row {
    display: flex;
    align-items: center;
    gap: 0.35rem;
}
.required-star {
    color: var(--error);
    font-weight: 600;
}
.info-tooltip {
    position: relative;
    cursor: help;
    color: var(--outline);
    font-size: 12px;
    line-height: 1;
}
.info-tooltip::after {
    content: attr(data-tooltip);
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    bottom: 100%;
    margin-bottom: 0.35rem;
    padding: 0.5rem 0.75rem;
    background: var(--on-surface);
    color: var(--surface-container-lowest);
    font-size: 11px;
    font-weight: 400;
    white-space: normal;
    min-width: 200px;
    max-width: 320px;
    width: max-content;
    border-radius: 2px;
    pointer-events: none;
    opacity: 0;
    visibility: hidden;
    transition: opacity 0.15s ease, visibility 0.15s ease;
    z-index: 1000;
    box-shadow: var(--shadow-md);
    font-family: var(--font-body);
    text-transform: none;
    letter-spacing: normal;
}
.info-tooltip:hover::after,
.info-tooltip:focus::after {
    opacity: 1;
    visibility: visible;
}

/* Inline upload row */
.field-inline {
    display: flex;
    gap: 0.5rem;
    align-items: stretch;
}
.field-inline input[type="text"] {
    flex: 1;
    min-width: 0;
}
.upload-btn {
    background: var(--surface-container);
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 0 0.875rem;
    color: var(--on-surface-variant);
    font-size: 0.75rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    gap: 0.35rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.upload-btn:hover {
    background: var(--surface-container-lowest);
    border-color: var(--primary);
    color: var(--primary);
}
.hidden-upload {
    display: none;
}
.upload-filename {
    font-size: 11px;
    color: var(--primary);
    margin-top: 0.25rem;
}

/* ── Drag-drop zone ───────────────────────────────────────────────── */
.drop-zone {
    position: relative;
    border: 2px dashed rgba(199, 196, 215, 0.5);
    border-radius: 8px;
    padding: 2rem 1.5rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s ease, background 0.2s ease;
    background: transparent;
    min-height: 160px;
}
.drop-zone:hover {
    border-color: rgba(67, 67, 213, 0.5);
    background: rgba(67, 67, 213, 0.03);
}
.drop-zone-icon {
    color: var(--primary);
    font-size: 2rem;
    margin-bottom: 0.5rem;
}
.drop-zone-text {
    font-size: 0.8125rem;
    font-weight: 600;
    color: var(--on-surface);
}
.drop-zone-hint {
    font-size: 10px;
    color: var(--outline);
    margin-top: 0.25rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* ── Spec browser ─────────────────────────────────────────────────── */
.spec-breadcrumb {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 0.8125rem;
    color: var(--outline);
    padding: 4px 0;
    flex-wrap: wrap;
}
.spec-breadcrumb-link {
    color: var(--primary);
    cursor: pointer;
    text-decoration: none;
}
.spec-breadcrumb-link:hover { text-decoration: underline; }
.spec-breadcrumb-sep { color: var(--outline-variant); margin: 0 2px; }
.spec-breadcrumb-current { color: var(--on-surface); font-weight: 600; }
.spec-file-list {
    border: 1px solid var(--ghost-border);
    border-radius: 8px;
    max-height: 220px;
    overflow-y: auto;
    background: var(--surface-container-lowest);
}
.spec-file-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    font-size: 0.8125rem;
    cursor: pointer;
    transition: background 0.1s;
}
.spec-file-item + .spec-file-item {
    border-top: 1px solid var(--ghost-border);
}
.spec-file-item:hover { background: var(--surface-container-low); }
.spec-file-item.selected { background: var(--primary-fixed); }
.spec-file-icon { flex-shrink: 0; width: 18px; text-align: center; }
.spec-file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.spec-file-size { color: var(--outline); font-size: 0.75rem; flex-shrink: 0; }
.spec-file-empty {
    padding: 24px 12px;
    text-align: center;
    color: var(--outline);
    font-size: 0.8125rem;
}
.spec-actions-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-top: 8px;
    flex-wrap: wrap;
}
.spec-upload-status {
    font-size: 0.8125rem;
    color: var(--outline);
}
.spec-validation-error {
    background: rgba(186, 26, 26, 0.05);
    border-left: 3px solid var(--error);
    border-radius: 2px;
    padding: 0.5rem 0.75rem;
    margin-top: 0.375rem;
    font-size: 0.8125rem;
    color: var(--error);
}
.spec-validation-error ul { color: var(--on-surface); }

/* Spec info rows */
.spec-info {
    padding-top: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
.spec-info-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.8125rem;
}
.spec-info-label {
    color: var(--outline);
    font-weight: 400;
}
.spec-info-value {
    font-family: var(--font-headline);
    font-weight: 700;
    color: var(--primary);
}
.spec-info-badge {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 2px 8px;
    border-radius: 0px;
}
.spec-info-badge-valid {
    background: rgba(40, 164, 100, 0.1);
    color: var(--success);
}
.spec-info-badge-invalid {
    background: rgba(186, 26, 26, 0.1);
    color: var(--error);
}

/* ── Checkbox ─────────────────────────────────────────────────────── */
.checkbox-field {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    margin-bottom: 1rem;
    cursor: pointer;
}
.checkbox-field input[type="checkbox"] {
    width: 1rem;
    height: 1rem;
    accent-color: var(--primary);
    cursor: pointer;
    border-radius: 2px;
}
.checkbox-field span {
    color: var(--on-surface);
    font-size: 0.8125rem;
    font-weight: 500;
}
.field-hint {
    margin-top: -0.5rem;
    margin-bottom: 1rem;
    padding-left: 1.625rem;
}
.field-hint.hidden { display: none; }

/* ── Advanced / Collapsible Sections ──────────────────────────────── */
.advanced-section {
    margin-top: 0.75rem;
    background: var(--surface-container-low);
    border-radius: 8px;
    overflow: hidden;
}
.advanced-section summary {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--on-surface-variant);
    cursor: pointer;
    padding: 0.75rem 1rem;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    transition: color 0.2s ease, background 0.2s ease;
}
.advanced-section summary::-webkit-details-marker {
    display: none;
}
.advanced-section summary::before {
    content: '\u25B6';
    font-size: 8px;
    transition: transform 0.2s ease;
}
.advanced-section[open] summary::before {
    transform: rotate(90deg);
}
.advanced-section summary:hover {
    color: var(--primary);
    background: var(--surface-container);
}
.advanced-summary-desc {
    font-weight: 400;
    font-size: 11px;
    color: var(--outline);
    letter-spacing: normal;
    text-transform: none;
}
.advanced-content {
    padding: 0.75rem 1rem 1rem;
}
.advanced-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 0.75rem;
}
.advanced-grid .field {
    margin-bottom: 0;
    min-width: 0;
}
.advanced-grid .field input[type="number"] {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
}

/* Filter section */
.filter-section {
    margin-top: 1rem;
    padding-top: 1rem;
}
.filter-section-title {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--on-surface-variant);
    margin-bottom: 0.75rem;
}
.filter-section-desc {
    font-size: 0.8rem;
    color: var(--on-surface-variant);
    margin-bottom: 0.75rem;
    margin-top: -0.5rem;
}
.notebook-hint {
    padding-left: 1.625rem;
    margin-top: -0.5rem;
}

/* ── API key source radio ─────────────────────────────────────────── */
.api-key-source {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}
.api-key-source-option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8125rem;
    color: var(--on-surface);
    cursor: pointer;
}
.api-key-source-option input[type="radio"] {
    accent-color: var(--primary);
    cursor: pointer;
}
.api-key-callout {
    background: rgba(144, 68, 0, 0.06);
    border-left: 3px solid var(--tertiary);
    border-radius: 2px;
    padding: 0.5rem 0.75rem;
    font-size: 0.8rem;
    color: var(--on-surface);
    margin-top: 0.5rem;
    display: none;
}

/* ── Domino job link ──────────────────────────────────────────────── */
.domino-job-link-row {
    margin-bottom: 0.5rem;
}
.domino-job-link {
    font-size: 0.8125rem;
    font-weight: 600;
    color: var(--primary);
}

/* ── Primary Button ───────────────────────────────────────────────── */
button.primary {
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-container) 100%);
    border: none;
    border-radius: 8px;
    padding: 0.875rem 1.5rem;
    color: var(--on-primary);
    font-family: var(--font-headline);
    font-size: 0.875rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 8px 24px rgba(67, 67, 213, 0.25);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
}
button.primary:hover {
    box-shadow: 0 12px 32px rgba(67, 67, 213, 0.3);
    transform: scale(0.98);
}
button.primary:active {
    transform: scale(0.96);
    box-shadow: 0 4px 16px rgba(67, 67, 213, 0.2);
}

/* ── Terminal Card ────────────────────────────────────────────────── */
.terminal-card {
    background: var(--surface-container-lowest);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    box-shadow: var(--shadow-sm);
}
.terminal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
}
.terminal-header h3 {
    font-family: var(--font-headline);
    font-size: 0.875rem;
    font-weight: 700;
}
.terminal-actions {
    display: flex;
    gap: 0.5rem;
}
.terminal-action {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--on-surface-variant);
    text-decoration: none;
    cursor: pointer;
    padding: 0.375rem 0.75rem;
    border-radius: 2px;
    background: var(--surface-container);
    border: none;
    transition: all 0.2s ease;
}
.terminal-action:hover {
    color: var(--primary);
    background: var(--surface-container-low);
}
.terminal-action-disabled {
    opacity: 0.35;
    pointer-events: none;
}

/* Status badges */
.terminal-status {
    display: inline-block;
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 3px 10px;
    border-radius: 0px;
    margin-bottom: 0.75rem;
}
.terminal-status-idle {
    background: var(--surface-container);
    color: var(--outline);
}
.terminal-status-running {
    background: rgba(67, 67, 213, 0.1);
    color: var(--primary);
}
.terminal-status-completed {
    background: rgba(40, 164, 100, 0.1);
    color: var(--success);
}
.terminal-status-failed {
    background: rgba(186, 26, 26, 0.1);
    color: var(--error);
}
.terminal-status-cancelled {
    background: rgba(144, 68, 0, 0.1);
    color: var(--warning);
}
.terminal-status-queued {
    background: rgba(144, 68, 0, 0.1);
    color: var(--warning);
}
.terminal-status-submitted {
    background: rgba(67, 67, 213, 0.1);
    color: var(--primary);
}
.terminal-status-succeeded {
    background: rgba(40, 164, 100, 0.1);
    color: var(--success);
}

/* ── Progress Phases ──────────────────────────────────────────────── */
.progress-phases {
    display: flex;
    gap: 0.375rem;
    margin-bottom: 0.75rem;
    padding: 0.625rem;
    background: var(--surface-container-low);
    border-radius: 8px;
}
.phase-item {
    flex: 1;
    min-width: 0;
}
.phase-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.3rem;
}
.phase-name {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    color: var(--outline);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.phase-pct {
    font-size: 10px;
    color: var(--outline);
    font-family: ui-monospace, monospace;
}
.phase-check {
    color: var(--success);
    font-size: 10px;
}
.phase-bar {
    height: 3px;
    background: var(--surface-container-high);
    border-radius: 1px;
    overflow: hidden;
}
.phase-bar-fill {
    height: 100%;
    background: var(--primary);
    border-radius: 1px;
    transition: width 0.3s ease;
}
.phase-bar-complete .phase-bar-fill {
    background: var(--success);
}
@keyframes shimmer {
    0% { background-position: 100% center; }
    100% { background-position: 0% center; }
}
.phase-active .phase-name {
    background: linear-gradient(
        90deg,
        rgba(67, 67, 213, 0.5) 0%,
        rgba(67, 67, 213, 0.5) 40%,
        rgba(67, 67, 213, 1) 50%,
        rgba(67, 67, 213, 0.5) 60%,
        rgba(67, 67, 213, 0.5) 100%
    );
    background-size: 200% 100%;
    background-clip: text;
    -webkit-background-clip: text;
    color: transparent;
    animation: shimmer 2s linear infinite;
}
.phase-complete .phase-name {
    color: var(--success);
}
.phase-pending .phase-name {
    color: var(--surface-dim);
}

/* ── Dark Terminal Console ────────────────────────────────────────── */
.terminal {
    background: #0f172a;
    border-radius: 0 0 12px 12px;
    padding: 1rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11px;
    line-height: 1.65;
    color: #cbd5e1;
    min-height: 0;
    max-height: 360px;
    overflow-y: auto;
    white-space: pre-wrap;
    margin-top: 0.5rem;
    box-shadow: 0 12px 40px rgba(15, 23, 42, 0.3);
}
.terminal-idle {
    min-height: 0;
    color: #475569;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-body);
    font-size: 0.8125rem;
    border-radius: 12px;
}
.terminal-line-active {
    color: #8888ff;
}
.terminal-line-complete {
    color: #4ADE80;
}
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
}
/* Only animate new log lines appended via JS, not on HTMX swap */
.log-line.new {
    animation: fadeIn 0.2s ease forwards;
}
/* Suppress transitions inside HTMX-swapped panels to prevent flicker */
#status-panel * ,
#job-history-content * {
    animation: none !important;
}

/* ── Download Buttons ─────────────────────────────────────────────── */
.download-section {
    display: flex;
    gap: 0.75rem;
    margin: 0.75rem 0;
    flex-wrap: wrap;
}
.download-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem;
    background: var(--success);
    color: white;
    border-radius: 2px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    text-decoration: none;
    transition: all 0.2s ease;
    box-shadow: var(--shadow-sm);
}
.download-btn:hover {
    filter: brightness(0.9);
    color: white;
    box-shadow: var(--shadow-md);
}
.download-btn::before {
    content: '\u2193';
    font-size: 1rem;
}
.download-btn-secondary {
    background: var(--surface-container-lowest);
    color: var(--success);
    border: 1.5px solid var(--success);
    box-shadow: none;
}
.download-btn-secondary:hover {
    background: rgba(40, 164, 100, 0.05);
    color: var(--success);
    filter: none;
}
.download-btn-secondary::before {
    content: '\u2193';
    font-size: 1rem;
}

/* ── Output Panel ─────────────────────────────────────────────────── */
.output-panel {
    flex: 1;
    min-height: 0;
    background: var(--surface-container-lowest);
    border-radius: 12px;
    box-shadow: var(--shadow-sm);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    padding: 1rem;
}
/* ── Job History ──────────────────────────────────────────────────── */
.job-history-section {
    margin-top: 1rem;
}
.job-history-section summary {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--on-surface-variant);
    cursor: pointer;
    padding: 0.75rem 1rem;
    background: var(--surface-container-lowest);
    border: 1px solid var(--ghost-border);
    border-radius: 4px;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.job-history-section summary::-webkit-details-marker { display: none; }
.job-history-section summary::before {
    content: '\u25B6';
    font-size: 8px;
    transition: transform 0.2s ease;
}
.job-history-section[open] summary::before { transform: rotate(90deg); }
.job-history-section[open] summary {
    border-radius: 4px 4px 0 0;
}
.job-history-content {
    background: var(--surface-container-lowest);
    border: 1px solid var(--ghost-border);
    border-top: none;
    border-radius: 0 0 4px 4px;
    padding: 1rem 1.25rem;
}
.history-empty {
    color: var(--outline);
    font-size: 0.8125rem;
    margin: 0;
}
.history-table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}
.history-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.75rem;
    min-width: 420px;
}
.history-table th {
    text-align: left;
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--on-surface-variant);
    padding: 0 0.5rem 0.5rem 0;
    border-bottom: 1px solid var(--ghost-border);
    white-space: nowrap;
}
.history-table td {
    padding: 0.5rem 0.5rem 0.5rem 0;
    color: var(--on-surface);
    border-bottom: 1px solid var(--ghost-border);
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.history-table tr:last-child td { border-bottom: none; }
.history-status {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 0px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
.history-status-queued { background: rgba(144,68,0,0.1); color: var(--warning); }
.history-status-submitted { background: rgba(67,67,213,0.1); color: var(--primary); }
.history-status-pending { background: rgba(144,68,0,0.1); color: var(--warning); }
.history-status-running { background: rgba(67,67,213,0.1); color: var(--primary); }
.history-status-succeeded { background: rgba(40,164,100,0.1); color: var(--success); }
.history-status-failed { background: rgba(186,26,26,0.1); color: var(--error); }
.history-status-cancelled { background: rgba(144,68,0,0.1); color: var(--warning); }
.history-toggle {
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--primary);
    cursor: pointer;
    padding: 0.375rem 0;
    margin-top: 0.25rem;
    list-style: none;
    user-select: none;
}
.history-toggle::-webkit-details-marker { display: none; }
.history-toggle::before {
    content: "\\25b6";
    display: inline-block;
    font-size: 0.5rem;
    margin-right: 0.375rem;
    transition: transform 0.15s ease;
    vertical-align: middle;
}
details[open] > .history-toggle::before {
    transform: rotate(90deg);
}
.history-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.75rem;
}

/* ── Card Footer (Generate button) ────────────────────────────────── */
.card-footer {
    display: flex;
    justify-content: flex-end;
    padding-top: 1rem;
    margin-top: 0.5rem;
    border-top: 1px solid var(--outline-variant);
}

/* ── OR Divider ──────────────────────────────────────────────────── */
.or-divider {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.5rem 0;
}
.or-divider::before,
.or-divider::after {
    content: '';
    flex: 1;
    border-top: 1px solid rgba(199, 196, 215, 0.3);
}
.or-divider-text {
    font-family: var(--font-body);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--outline);
}

/* ── Hardware Tier Card Grid ─────────────────────────────────────── */
.hw-tier-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.625rem;
}
.hw-tier-card {
    padding: 0.75rem;
    border: 1px solid rgba(199, 196, 215, 0.3);
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s ease;
    background: transparent;
}
.hw-tier-card:hover {
    background: var(--surface-container-low);
}
.hw-tier-card.selected {
    border-color: rgba(67, 67, 213, 0.3);
    background: rgba(67, 67, 213, 0.04);
}
.hw-tier-card-name {
    font-family: var(--font-body);
    font-size: 0.75rem;
    font-weight: 700;
    color: var(--on-surface);
}
.hw-tier-card.selected .hw-tier-card-name {
    color: var(--primary);
}
.hw-tier-card-detail {
    font-size: 10px;
    color: var(--on-surface-variant);
    margin-top: 0.125rem;
}

/* ── Insight card ─────────────────────────────────────────────────── */
.insight-card {
    background: var(--surface-container-high);
    border-left: 3px solid var(--primary);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-top: 1rem;
}
.insight-card h4 {
    font-family: var(--font-headline);
    font-size: 0.8125rem;
    font-weight: 700;
    color: var(--on-surface);
    margin-bottom: 0.25rem;
}
.insight-card p {
    font-size: 11px;
    line-height: 1.6;
    color: var(--on-surface-variant);
    margin: 0;
}

/* ── Gear settings button & modal ─────────────────────────────────── */
#gear-settings-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 100%;
    padding: 10px 16px;
    margin-top: 12px;
    background: var(--surface-container);
    color: var(--on-surface-variant);
    border: 1px solid var(--outline-variant);
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s;
}
#gear-settings-btn:hover {
    background: var(--surface-container-high);
}
#gear-settings-btn svg {
    flex-shrink: 0;
}

#gear-popover {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.35);
}
#gear-popover-inner {
    background: #fff;
    width: 420px;
    max-height: 80vh;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}
#gear-popover-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--outline-variant);
}
.gear-popover-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--on-surface);
}
.gear-popover-close {
    background: none;
    border: none;
    font-size: 18px;
    color: var(--on-surface-variant);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
    line-height: 1;
}
.gear-popover-close:hover {
    background: var(--surface-container);
}
#gear-popover-content {
    padding: 16px 20px 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

/* ── Responsive ───────────────────────────────────────────────────── */
"""
