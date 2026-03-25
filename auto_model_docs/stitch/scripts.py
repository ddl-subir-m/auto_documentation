"""All JavaScript code for the Stitch UI."""

from __future__ import annotations

import json
from pathlib import Path

from .state import _get_default_output_dir


def get_output_defaults_script() -> str:
    """Return the small inline script that sets output-dir JS constants."""
    return f"""
        const DOMINO_OUTPUT_DEFAULT = {json.dumps(str(_get_default_output_dir()))};
        const APP_OUTPUT_DEFAULT = "/mnt/code/output";
    """


SMART_POLLING_JS = r"""
    window.addEventListener('DOMContentLoaded', function() {
        var htmxWorking = false;
        if (typeof htmx !== 'undefined' && typeof htmx.ajax === 'function') {
            htmxWorking = true;
            console.log('htmx loaded and functional');
        } else {
            console.log('htmx not functional, using vanilla JS');
        }

        // Track last-known version so we only swap when something changed
        var _lastLogVersion = -1;
        var _pollActive = true;
        var TERMINAL_STATES = ['idle', 'completed', 'failed', 'cancelled', 'succeeded'];

        function getCurrentVersion() {
            var card = document.querySelector('#status-panel [data-log-version]');
            return card ? parseInt(card.dataset.logVersion, 10) : -1;
        }

        // Full fetch — replaces status panel HTML
        function fetchFullStatus() {
            var panel = document.getElementById('status-panel');
            if (!panel) return Promise.resolve();
            return fetch('status')
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    panel.innerHTML = html;
                    _lastLogVersion = getCurrentVersion();
                    // Fire the same event htmx would so styling hooks run
                    document.body.dispatchEvent(new CustomEvent('statusUpdated'));
                })
                .catch(function(e) { console.log('Status fetch error:', e); });
        }

        // Lightweight check — only fetches full HTML when version changed
        function smartPoll() {
            if (!_pollActive) return;
            fetch('status-check')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    var serverVersion = data.logVersion || 0;
                    if (serverVersion !== _lastLogVersion) {
                        fetchFullStatus();
                    }
                    // Stop polling when job reaches a terminal state
                    if (TERMINAL_STATES.indexOf(data.status) !== -1 && serverVersion === _lastLogVersion) {
                        _pollActive = false;
                    }
                })
                .catch(function(e) { console.log('Status check error:', e); });
        }

        // Initialise version from DOM
        _lastLogVersion = getCurrentVersion();

        // Start smart polling for app mode
        var formEl = document.getElementById('main-form');
        var inferredMode = formEl ? formEl.getAttribute('data-execution-mode') : 'app';
        if (inferredMode !== 'domino') {
            setInterval(smartPoll, 2000);
        }

        // Re-activate polling when a new job starts (after form submit)
        window._activateStatusPolling = function() {
            _pollActive = true;
            _lastLogVersion = -1; // Force an immediate update
            _tabInitialized = false; // Reset so next swap shows Output tab
            showOutputTab('live'); // Immediately show Output tab on new job
        };

        // Direct click handler on Generate button
        var generateBtn = document.getElementById('generate-btn');
        if (generateBtn) {
            generateBtn.addEventListener('click', function(e) {
                if (htmxWorking) return;
                e.preventDefault();
                e.stopPropagation();
                // Block submission if spec validation failed
                if (window._specValid === false) {
                    var resultEl = document.getElementById('spec-validation-result');
                    if (resultEl) resultEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    return;
                }
                var form = document.querySelector('form');
                if (!form) return;
                var formData = new FormData(form);
                generateBtn.disabled = true;
                generateBtn.textContent = 'Starting...';
                fetch('run', { method: 'POST', body: formData })
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    var panel = document.getElementById('status-panel');
                    if (panel) panel.innerHTML = html;
                    generateBtn.disabled = false;
                    generateBtn.textContent = 'Generate Documentation';
                    window._activateStatusPolling();
                })
                .catch(function(e) {
                    console.log('Form submit error:', e);
                    generateBtn.disabled = false;
                    generateBtn.textContent = 'Generate Documentation';
                });
            });
        }

        // Form submit backup
        var form = document.querySelector('form');
        if (form && !htmxWorking) {
            form.addEventListener('submit', function(e) {
                e.preventDefault();
                var btn = document.getElementById('generate-btn');
                if (btn) btn.click();
            });
        }

        // Stop and Clear button delegation
        document.addEventListener('click', function(e) {
            var target = e.target;
            if (target.textContent === 'Stop' && !target.classList.contains('terminal-action-disabled')) {
                if (htmxWorking) return;
                e.preventDefault();
                fetch('stop', { method: 'POST' })
                    .then(function(r) { return r.text(); })
                    .then(function(html) {
                        var panel = document.getElementById('status-panel');
                        if (panel) panel.innerHTML = html;
                        _lastLogVersion = getCurrentVersion();
                    });
            }
            if (target.textContent === 'Clear' && !target.classList.contains('terminal-action-disabled')) {
                if (htmxWorking) return;
                e.preventDefault();
                fetch('clear-terminal', { method: 'POST' })
                    .then(function(r) { return r.text(); })
                    .then(function(html) {
                        var panel = document.getElementById('status-panel');
                        if (panel) panel.innerHTML = html;
                        _lastLogVersion = getCurrentVersion();
                    });
            }
        });
    });
"""


MAIN_DOM_JS = r"""
    document.addEventListener('DOMContentLoaded', function() {

        // ── Auto-fill projectId from URL or postMessage ──
        // Domino Apps run inside a cross-origin iframe; the proxy strips
        // query params.  Try what we can; the user can always type it manually.
        (function() {
            function setProjectId(pid) {
                if (!pid) return;
                var input = document.getElementById('field-project-id');
                if (input && !input.value) {
                    input.value = pid;
                    input.dataset.autoDocSet = 'true';
                    input.dispatchEvent(new Event('change'));
                }
            }
            var pid = null;
            // Diagnostic: log origin info
            if (window.parent !== window) {
                try {
                } catch(e) {
                }
            }
            // 1. Own query string (direct / non-proxied access)
            pid = new URLSearchParams(window.location.search).get('projectId');
            // 2. Own hash fragment (#projectId=xxx — survives proxies)
            if (!pid && window.location.hash) {
                var h = window.location.hash.substring(1);
                if (h.charAt(0) === '?') h = h.substring(1);
                pid = new URLSearchParams(h).get('projectId');
            }
            // 3. Parent frame (same-origin deployments where parent
            //    and iframe share the same host)
            if (!pid && window.parent !== window) {
                try {
                    var pLoc = window.parent.location;
                    pid = new URLSearchParams(pLoc.search).get('projectId');
                    if (!pid && pLoc.hash) {
                        var ph = pLoc.hash.substring(1);
                        if (ph.charAt(0) === '?') ph = ph.substring(1);
                        pid = new URLSearchParams(ph).get('projectId');
                    }
                } catch(e) { /* cross-origin — ignore */ }
            }
            if (pid) {
                setProjectId(pid);
            }
            // 4. Listen for postMessage from Domino parent frame
            window.addEventListener('message', function(e) {
                if (e.data && typeof e.data === 'object' && e.data.projectId) {
                    setProjectId(e.data.projectId);
                }
            });
        })();

        // ── Language detection ────────────────────────────────────────────
        var langRow = document.getElementById('lang-detection-row');
        var langName = document.getElementById('lang-detected-name');
        var langCount = document.getElementById('lang-detected-count');
        var langInput = document.getElementById('field-detected-language');
        var langSelect = document.getElementById('lang-override-select');

        function detectLanguage(codeRoot) {
            var url = 'api/detect-language';
            if (codeRoot) url += '?code_root=' + encodeURIComponent(codeRoot);
            fetch(url)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (langRow) langRow.style.display = '';
                    if (data.language) {
                        if (langName) langName.textContent = data.display_name;
                        if (langCount) langCount.textContent = '(' + data.file_count + ' files)';
                        if (langInput) langInput.value = data.language;
                        if (langSelect) langSelect.value = data.language;
                    } else {
                        if (langName) langName.textContent = '';
                        if (langCount) langCount.textContent = '';
                        if (langRow) {
                            langRow.innerHTML = '<span style="color:#767586;">No supported source files found. Supports Python, R, SAS, MATLAB.</span>';
                            langRow.style.display = '';
                        }
                    }
                })
                .catch(function() {});
        }

        window.handleLanguageOverride = function(lang) {
            if (langInput) langInput.value = lang;
            var cr = document.getElementById('field-code_root');
            detectLanguage(cr ? cr.value : undefined);
        };

        function detectLanguageFromCodeRoot() {
            var cr = document.getElementById('field-code_root');
            detectLanguage(cr ? cr.value : undefined);
        }

        detectLanguageFromCodeRoot();

        // ── All DOM references declared up-front to avoid TDZ errors ──────
        // Mode toggle removed — mode is auto-inferred server-side
        const uploadBtnLabel    = document.querySelector('label.upload-btn');
        // specSavedName removed — replaced by dataset browser UI
        const appModeNote       = document.getElementById('app-mode-note');
        const appNoteHint       = document.getElementById('app-mode-notebook-hint');
        const apiKeyPassField   = document.getElementById('api-key-pass-field');
        const apiKeyCallout     = document.getElementById('api-key-callout');
        const apiKeySourceRadios = document.querySelectorAll('input[name="api_key_source"]');
        const providerSelect    = document.getElementById('field-provider');
        const baseUrlField      = document.getElementById('base-url-field');
        const modelNameField    = document.getElementById('model-name-field');

        // ── Mode is server-rendered (no toggle) ─────────────────────────
        // Domino fields are conditionally rendered server-side.
        // Nothing to toggle at runtime.

        // ── API key source radio ───────────────────────────────────────────
        function applyApiKeySource(src) {
            const show = src === 'pass_now';
            if (apiKeyPassField) apiKeyPassField.style.display = show ? '' : 'none';
            if (apiKeyCallout) {
                apiKeyCallout.style.display = show ? 'block' : 'none';
                if (!apiKeyCallout.textContent.trim()) {
                    apiKeyCallout.textContent = '\u26a0 This key will be visible in the Domino job\u2019s environment metadata to project admins.';
                }
            }
        }

        apiKeySourceRadios.forEach(function(r) {
            r.addEventListener('change', function() { applyApiKeySource(this.value); });
        });

        const checkedSrc = document.querySelector('input[name="api_key_source"]:checked');
        applyApiKeySource(checkedSrc ? checkedSrc.value : 'domino_env');

        // ── Resolve project, refresh tiers & output dir on change ─────
        var projectIdInput = document.getElementById('field-project-id');
        if (projectIdInput) {
            var refreshTimer = null;
            function onProjectIdChange() {
                clearTimeout(refreshTimer);
                refreshTimer = setTimeout(function() {
                    var pid = projectIdInput.value.trim();
                    var qs = pid ? '?projectId=' + encodeURIComponent(pid) : '';
                    // Resolve project name
                    fetch('api/resolve-project' + qs)
                        .then(function(r) { return r.text(); })
                        .then(function(html) {
                            var el = document.getElementById('project-id-resolved');
                            if (el) el.outerHTML = html;
                            // Update output dir from resolved name
                            var newEl = document.getElementById('project-id-resolved');
                            var name = newEl ? newEl.getAttribute('data-project-name') : null;
                            var outputDir = document.getElementById('field-output_dir');
                            if (outputDir) {
                                outputDir.value = name ? '/mnt/data/' + name : DOMINO_OUTPUT_DEFAULT;
                            }
                        })
                        .catch(function() {});
                    // Refresh hardware tiers
                    if (typeof htmx !== 'undefined') {
                        htmx.ajax('GET', 'api/hardware-tiers' + qs, {
                            target: '#field-hardware_tier',
                            swap: 'outerHTML'
                        });
                    }
                }, 300);
            }
            projectIdInput.addEventListener('change', onProjectIdChange);
            projectIdInput.addEventListener('blur', onProjectIdChange);
        }

        // ── Dataset spec browser (Domino mode) ───────────────────────────
        var specDatasetSelect = document.getElementById('spec-dataset-select');
        var specFileList = document.getElementById('spec-file-list');
        var specBreadcrumb = document.getElementById('spec-breadcrumb');
        var specSelectedIndicator = document.getElementById('spec-selected-indicator');
        var specSelectedName = document.getElementById('spec-selected-name');
        var specMachineUpload = document.getElementById('spec-machine-upload');
        var specUploadStatus = document.getElementById('spec-upload-status');
        var specPathHidden = document.getElementById('field-spec_path');

        // State
        var _specDatasets = [];
        var _specCurrentDatasetId = '';
        var _specCurrentDatasetName = '';
        var _specCurrentSnapshotId = '';
        var _specCurrentPath = '';
        var _specAutoDocSpecsId = '';

        function getProjectIdParam() {
            var formEl = document.getElementById('main-form');
            var pid = '';
            // Check projectId from query string
            var params = new URLSearchParams(window.location.search);
            pid = params.get('projectId') || params.get('project_id') || '';
            // Also check the project-id field
            if (!pid) {
                var pidInput = document.getElementById('field-project-id');
                if (pidInput) pid = pidInput.value.trim();
            }
            return pid ? '&projectId=' + encodeURIComponent(pid) : '';
        }

        function loadDatasets() {
            if (!specDatasetSelect) return;
            console.log('[spec-browser] Loading writable datasets...');
            var qs = '?' + getProjectIdParam().replace(/^&/, '');
            fetch('api/datasets' + qs)
                .then(function(r) { return r.json(); })
                .then(function(datasets) {
                    if (datasets.error) {
                        console.error('[spec-browser] Error loading datasets:', datasets.error);
                        specDatasetSelect.innerHTML = '<option value="">Error: ' + datasets.error + '</option>';
                        return;
                    }
                    _specDatasets = datasets;
                    console.log('[spec-browser] Loaded ' + datasets.length + ' datasets:', datasets.map(function(d) { return d.name; }));
                    if (datasets.length === 0) {
                        specDatasetSelect.innerHTML = '<option value="">No datasets found for this project</option>';
                        console.warn('[spec-browser] No writable datasets returned — upload a spec file to auto-create one');
                        return;
                    }
                    var html = '<option value="">Choose a dataset...</option>';
                    for (var i = 0; i < datasets.length; i++) {
                        html += '<option value="' + datasets[i].id + '" data-name="' + datasets[i].name + '" data-snapshot="' + (datasets[i].rwSnapshotId || '') + '">'
                            + datasets[i].name + '</option>';
                    }
                    specDatasetSelect.innerHTML = html;

                    // Auto-select autodoc-specs if it exists
                    for (var j = 0; j < datasets.length; j++) {
                        if (datasets[j].name === 'autodoc-specs') {
                            specDatasetSelect.value = datasets[j].id;
                            _specAutoDocSpecsId = datasets[j].id;
                            onDatasetChange();
                            return;
                        }
                    }
                })
                .catch(function(err) {
                    console.error('[spec-browser] Failed to load datasets:', err);
                    specDatasetSelect.innerHTML = '<option value="">Failed to load datasets</option>';
                });
        }

        function onDatasetChange() {
            if (!specDatasetSelect) return;
            var opt = specDatasetSelect.options[specDatasetSelect.selectedIndex];
            console.log('[spec-browser] Dataset selected:', opt ? opt.getAttribute('data-name') : 'none');
            _specCurrentDatasetId = specDatasetSelect.value;
            _specCurrentDatasetName = opt ? opt.getAttribute('data-name') || '' : '';
            _specCurrentSnapshotId = opt ? opt.getAttribute('data-snapshot') || '' : '';
            _specCurrentPath = '';
            if (_specCurrentDatasetId) {
                browseFiles('');
            } else {
                if (specFileList) specFileList.innerHTML = '<span class="spec-file-empty">Select a dataset to browse spec files</span>';
                if (specBreadcrumb) specBreadcrumb.innerHTML = '';
            }
        }

        function browseFiles(path) {
            _specCurrentPath = path;
            if (!specFileList) return;
            console.log('[spec-browser] Browsing path:', path || '(root)', 'in dataset:', _specCurrentDatasetName);
            specFileList.innerHTML = '<span class="spec-file-empty">Loading...</span>';
            renderBreadcrumb(path);

            var qs = '?datasetId=' + encodeURIComponent(_specCurrentDatasetId);
            if (_specCurrentSnapshotId) qs += '&snapshotId=' + encodeURIComponent(_specCurrentSnapshotId);
            if (path) qs += '&path=' + encodeURIComponent(path);
            qs += getProjectIdParam();

            fetch('api/dataset-files' + qs)
                .then(function(r) { return r.json(); })
                .then(function(files) {
                    if (files.error) {
                        console.error('[spec-browser] File listing error:', files.error);
                        specFileList.innerHTML = '<span class="spec-file-empty">Error: ' + files.error + '</span>';
                        return;
                    }
                    console.log('[spec-browser] Found ' + files.length + ' items at path:', path || '(root)');
                    if (files.length === 0) {
                        specFileList.innerHTML = '<span class="spec-file-empty">No YAML files found in this location</span>';
                        return;
                    }
                    var html = '';
                    // Sort: directories first, then files
                    files.sort(function(a, b) {
                        if (a.isDirectory && !b.isDirectory) return -1;
                        if (!a.isDirectory && b.isDirectory) return 1;
                        return a.fileName.localeCompare(b.fileName);
                    });
                    for (var i = 0; i < files.length; i++) {
                        var f = files[i];
                        var icon = f.isDirectory ? '\ud83d\udcc1' : '\ud83d\udcc4';
                        var size = f.isDirectory ? '' : formatBytes(f.sizeInBytes || 0);
                        var fullPath = path ? path + '/' + f.fileName : f.fileName;
                        html += '<div class="spec-file-item" data-path="' + fullPath + '" data-dir="' + f.isDirectory + '" data-name="' + f.fileName + '">'
                            + '<span class="spec-file-icon">' + icon + '</span>'
                            + '<span class="spec-file-name">' + f.fileName + '</span>'
                            + '<span class="spec-file-size">' + size + '</span>'
                            + '</div>';
                    }
                    specFileList.innerHTML = html;

                    // Attach click handlers
                    var items = specFileList.querySelectorAll('.spec-file-item');
                    for (var j = 0; j < items.length; j++) {
                        items[j].addEventListener('click', onFileClick);
                    }
                })
                .catch(function() {
                    specFileList.innerHTML = '<span class="spec-file-empty">Failed to load files</span>';
                });
        }

        function onFileClick(e) {
            var el = e.currentTarget;
            var isDir = el.getAttribute('data-dir') === 'true';
            var path = el.getAttribute('data-path');
            if (isDir) {
                browseFiles(path);
            } else {
                // Select this file
                var items = specFileList.querySelectorAll('.spec-file-item');
                for (var i = 0; i < items.length; i++) items[i].classList.remove('selected');
                el.classList.add('selected');
                selectSpecFile(_specCurrentDatasetName, path);
            }
        }

        function selectSpecFile(datasetName, filePath) {
            console.log('[spec-browser] Selected:', datasetName + '/' + filePath);
            if (specSelectedIndicator) specSelectedIndicator.style.display = '';
            if (specSelectedName) specSelectedName.textContent = datasetName + '/' + filePath;
            // Build mount path and set the hidden form field
            // The server will resolve the correct mount prefix
            if (specPathHidden) {
                // Use a marker so the server knows this is a dataset reference
                specPathHidden.value = 'dataset://' + datasetName + '/' + filePath;
            }
        }

        function renderBreadcrumb(path) {
            if (!specBreadcrumb) return;
            var parts = path ? path.split('/').filter(Boolean) : [];
            var html = '<span class="spec-breadcrumb-link" onclick="window._specBrowse(\'\')">root</span>';
            var cumulative = '';
            for (var i = 0; i < parts.length; i++) {
                cumulative += (i > 0 ? '/' : '') + parts[i];
                html += '<span class="spec-breadcrumb-sep">/</span>';
                if (i === parts.length - 1) {
                    html += '<span class="spec-breadcrumb-current">' + parts[i] + '</span>';
                } else {
                    html += '<span class="spec-breadcrumb-link" onclick="window._specBrowse(\'' + cumulative + '\')">' + parts[i] + '</span>';
                }
            }
            specBreadcrumb.innerHTML = html;
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        // Global for breadcrumb onclick
        window._specBrowse = function(path) { browseFiles(path); };

        // Upload from machine → autodoc-specs dataset
        if (specMachineUpload) {
            specMachineUpload.addEventListener('change', function(e) {
                var file = e.target.files[0];
                if (!file) return;
                console.log('[spec-browser] Upload from machine:', file.name, '(' + file.size + ' bytes)');
                if (specUploadStatus) { specUploadStatus.textContent = 'Uploading ' + file.name + '...'; specUploadStatus.style.color = ''; }
                // Validate spec content before uploading
                if (typeof validateSpecContent === 'function') validateSpecContent(file);

                // Ensure autodoc-specs dataset exists, then upload
                var qs = '?' + getProjectIdParam().replace(/^&/, '');
                fetch('api/ensure-autodoc-specs' + qs, { method: 'POST' })
                    .then(function(r) { return r.json(); })
                    .then(function(ds) {
                        if (ds.error) throw new Error(ds.error);
                        console.log('[spec-browser] autodoc-specs dataset ensured: id=' + ds.id);
                        _specAutoDocSpecsId = ds.id;
                        var fd = new FormData();
                        fd.append('datasetId', ds.id);
                        fd.append('datasetName', ds.name || 'autodoc-specs');
                        fd.append('file', file);
                        return fetch('api/upload-spec-to-dataset' + qs, { method: 'POST', body: fd });
                    })
                    .then(function(r) { return r.json(); })
                    .then(function(result) {
                        if (result.error) throw new Error(result.error);
                        console.log('[spec-browser] Upload success:', result.fileName, '→', result.mountPath);
                        if (specUploadStatus) { specUploadStatus.textContent = 'Uploaded: ' + result.fileName; specUploadStatus.style.color = '#2e7d32'; }
                        // Select the uploaded file
                        selectSpecFile('autodoc-specs', result.fileName);
                        // Refresh datasets if autodoc-specs was just created
                        loadDatasets();
                    })
                    .catch(function(err) {
                        console.error('[spec-browser] Upload failed:', err.message);
                        if (specUploadStatus) { specUploadStatus.textContent = 'Upload failed: ' + err.message; specUploadStatus.style.color = '#ba1a1a'; }
                    });
            });
        }

        // Wire dataset select change
        if (specDatasetSelect) {
            specDatasetSelect.addEventListener('change', onDatasetChange);
            loadDatasets();
        }

        // ── Toggle base URL and model name fields based on provider selection
        var OPENAI_DEFAULT_MODEL = 'kimi-k2-0905-preview';
        var ANTHROPIC_DEFAULT_MODEL = 'claude-sonnet-4-20250514';
        function toggleOpenAIFields() {
            const isOpenAI = providerSelect && providerSelect.value === 'openai';
            if (baseUrlField) {
                baseUrlField.style.display = isOpenAI ? 'flex' : 'none';
            }
            if (modelNameField) {
                modelNameField.style.display = isOpenAI ? 'flex' : 'none';
            }
            var modelInput = document.getElementById('field-model');
            if (modelInput) {
                if (isOpenAI) {
                    if (!modelInput.value || modelInput.value === ANTHROPIC_DEFAULT_MODEL) {
                        modelInput.value = OPENAI_DEFAULT_MODEL;
                    }
                } else {
                    if (!modelInput.value || modelInput.value === OPENAI_DEFAULT_MODEL) {
                        modelInput.value = ANTHROPIC_DEFAULT_MODEL;
                    }
                }
            }
        }

        if (providerSelect) {
            providerSelect.addEventListener('change', toggleOpenAIFields);
            toggleOpenAIFields();
        }

        // Handle file upload and update spec path display (app mode)
        var specUploadApp = document.querySelector('input[name="spec_upload"]');
        var specPathDisplay = document.getElementById('field-spec_path_display');
        var specPathHiddenApp = document.getElementById('field-spec_path');
        var uploadFilenameEl = document.getElementById('upload-filename');

        // ── Spec validation helper ────────────────────────────────────
        window._specValid = true; // tracks latest validation state
        function validateSpecContent(file) {
            var fd = new FormData();
            fd.append('spec_upload', file);
            var resultEl = document.getElementById('spec-validation-result');
            if (resultEl) resultEl.innerHTML = '<span style="color:var(--outline);font-size:0.8125rem;">Validating spec...</span>';
            fetch('validate-spec', { method: 'POST', body: fd })
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    if (resultEl) resultEl.outerHTML = html;
                    // Check if validation passed
                    window._specValid = html.indexOf('validation failed') === -1;
                })
                .catch(function() {
                    if (resultEl) resultEl.innerHTML = '';
                    window._specValid = true; // don't block on network errors
                });
        }

        if (specUploadApp && specPathDisplay) {
            specUploadApp.addEventListener('change', function(e) {
                var file = e.target.files[0];
                if (file) {
                    specPathDisplay.value = '[Uploaded] ' + file.name;
                    specPathDisplay.disabled = true;
                    if (specPathHiddenApp) specPathHiddenApp.value = '[Uploaded] ' + file.name;
                    if (uploadFilenameEl) uploadFilenameEl.textContent = 'Using uploaded file: ' + file.name;
                    validateSpecContent(file);
                } else {
                    specPathDisplay.disabled = false;
                    if (uploadFilenameEl) uploadFilenameEl.textContent = '';
                    var resultEl = document.getElementById('spec-validation-result');
                    if (resultEl) resultEl.innerHTML = '';
                    window._specValid = true;
                }
            });
        }

        // Highlight active terminal lines with spinner
        function styleTerminalLines() {
            const terminal = document.querySelector('.terminal:not(.terminal-idle)');
            if (!terminal) return;

            const text = terminal.textContent;
            const lines = text.split('\n');
            const totalLines = lines.length;

            // Check if job is still running (look for completion indicators)
            const isComplete = lines.some(line =>
                line.includes('Generation complete') ||
                line.includes('Error:') ||
                line.includes('Cancelled') ||
                line.includes('Cleanup complete')
            );

            // Find last active line index (the most recent activity)
            let lastActiveIndex = -1;
            if (!isComplete) {
                for (let i = lines.length - 1; i >= 0; i--) {
                    const line = lines[i].trim();
                    if (line && line.match(/^\[\d{2}:\d{2}:\d{2}\]/)) {
                        lastActiveIndex = i;
                        break;
                    }
                }
            }

            // Style the lines
            let styledHtml = lines.map((line, index) => {
                const escapedLine = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');

                // Show spinner on the last active timestamped line
                if (index === lastActiveIndex) {
                    return '<span class="terminal-line-active">' + escapedLine + '</span>';
                }
                // Style completion messages
                if (line.includes('Complete') || line.includes('Generation complete')) {
                    return '<span class="terminal-line-complete">' + escapedLine + '</span>';
                }
                return escapedLine;
            }).join('\n');

            terminal.innerHTML = styledHtml;
        }

        // Auto-scroll terminal to bottom to show latest logs
        function scrollTerminalToBottom() {
            const terminal = document.querySelector('.terminal:not(.terminal-idle)');
            if (terminal) {
                terminal.scrollTop = terminal.scrollHeight;
            }
        }

        // Tab switcher — on window so inline onclick can call it
        window.showOutputTab = function(tab) {
            document.querySelectorAll('.tab-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.tab === tab);
            });
            document.querySelectorAll('.tab-content').forEach(function(content) {
                content.classList.toggle('hidden', content.id !== 'tab-' + tab);
            });
        };

        // ── Code root prefix+suffix sync ──────────────────────────────────
        (function() {
            const prefix = document.getElementById('code-root-prefix');
            const suffix = document.getElementById('code-root-suffix');
            const hidden = document.getElementById('field-code_root');
            function sync() {
                if (!prefix || !hidden) return;
                const base = prefix.textContent.trim();
                const sub = suffix ? suffix.value.replace(/^\/+/, '') : '';
                hidden.value = sub ? base + '/' + sub : base;
            }
            if (suffix) {
                suffix.addEventListener('input', sync);
                var langTimer = null;
                suffix.addEventListener('input', function() {
                    clearTimeout(langTimer);
                    langTimer = setTimeout(function() { detectLanguageFromCodeRoot(); }, 400);
                });
            }
            sync();
        })();

        // Run styling/scrolling after any status update (HTMX swap or smart poll)
        function onStatusUpdate() {
            styleTerminalLines();
            scrollTerminalToBottom();
        }

        // One-shot flag: show Output tab only after a NEW job submit
        // (when _activateStatusPolling resets it to false), not on page load polling.
        var _tabInitialized = true;
        document.body.addEventListener('htmx:afterSwap', function(e) {
            var targetId = e.detail && e.detail.target ? e.detail.target.id : '';
            // Only process status-panel swaps — ignore job-history swaps
            if (targetId === 'status-panel') {
                if (!_tabInitialized) {
                    showOutputTab('live');
                    _tabInitialized = true;
                }
                onStatusUpdate();
            }
        });

        // Custom event fired by smart polling after DOM update
        document.body.addEventListener('statusUpdated', onStatusUpdate);

        // Re-activate smart polling when HTMX submits the form
        document.body.addEventListener('htmx:afterRequest', function(e) {
            if (e.detail && e.detail.pathInfo && e.detail.pathInfo.requestPath === '/run') {
                if (typeof window._activateStatusPolling === 'function') {
                    window._activateStatusPolling();
                }
            }
        });

        setInterval(styleTerminalLines, 500);

        // Poll job history via plain fetch (not HTMX) to avoid ID-settling interference
        setInterval(function() {
            var el = document.getElementById('job-history-content');
            if (!el) return;
            fetch('job-history')
                .then(function(r) { return r.text(); })
                .then(function(html) { el.innerHTML = html; })
                .catch(function() {});
        }, 15000);
    });
"""
