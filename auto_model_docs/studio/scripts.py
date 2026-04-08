"""All JavaScript code for the Stitch UI."""

from __future__ import annotations


MAIN_DOM_JS = r"""
    // ── Shared fetch helper: check response status before parsing ──
    function _checkResp(r) {
        if (!r.ok) throw new Error('Server error (' + r.status + ')');
        return r;
    }

    // ── Hardware tier card selection ──
    function selectHwTier(card, tierId) {
        var grid = card.closest('.hw-tier-grid');
        grid.querySelectorAll('.hw-tier-card').forEach(function(c) { c.classList.remove('selected'); });
        card.classList.add('selected');
        document.getElementById('field-hardware_tier').value = tierId;
    }

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
                .then(_checkResp).then(function(r) { return r.json(); })
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

        // ── DOM references ──────────────────────────────────────────────
        const apiKeyPassField   = document.getElementById('api-key-pass-field');
        const apiKeyCallout     = document.getElementById('api-key-callout');
        const apiKeySourceRadios = document.querySelectorAll('input[name="api_key_source"]');
        const providerSelect    = document.getElementById('field-provider');
        const baseUrlField      = document.getElementById('base-url-field');
        const modelNameField    = document.getElementById('model-name-field');

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
                        .then(_checkResp).then(function(r) { return r.text(); })
                        .then(function(html) {
                            var el = document.getElementById('project-id-resolved');
                            if (el) el.outerHTML = html;
                            // Output location is fixed (artifacts → docs/)
                            // No need to update the display field.
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

        // ── Artifact spec browser ─────────────────────────────────────────
        var specFileList = document.getElementById('spec-file-list');
        var specSelectedIndicator = document.getElementById('spec-selected-indicator');
        var specSelectedName = document.getElementById('spec-selected-name');
        var specMachineUpload = document.getElementById('spec-machine-upload');
        var specUploadStatus = document.getElementById('spec-upload-status');
        var specPathHidden = document.getElementById('field-spec_path');

        function getProjectIdParam() {
            var params = new URLSearchParams(window.location.search);
            var pid = params.get('projectId') || params.get('project_id') || '';
            if (!pid) {
                var pidInput = document.getElementById('field-project-id');
                if (pidInput) pid = pidInput.value.trim();
            }
            return pid ? '&projectId=' + encodeURIComponent(pid) : '';
        }

        function loadSpecs() {
            if (!specFileList) return;
            console.log('[spec-browser] Loading spec files from artifacts...');
            specFileList.innerHTML = '<span class="spec-file-empty">Loading...</span>';
            var qs = '?' + getProjectIdParam().replace(/^&/, '');
            fetch('api/spec-files' + qs)
                .then(_checkResp).then(function(r) { return r.json(); })
                .then(function(files) {
                    console.log('[spec-browser] Found ' + files.length + ' spec files');
                    if (files.length === 0) {
                        specFileList.innerHTML = '<span class="spec-file-empty">No spec files found. Upload one below.</span>';
                        return;
                    }
                    var html = '';
                    for (var i = 0; i < files.length; i++) {
                        var f = files[i];
                        var size = formatBytes(f.size || 0);
                        html += '<div class="spec-file-item" data-path="' + f.path + '" data-name="' + f.name + '">'
                            + '<span class="spec-file-icon">\ud83d\udcc4</span>'
                            + '<span class="spec-file-name">' + f.name + '</span>'
                            + '<span class="spec-file-size">' + size + '</span>'
                            + '</div>';
                    }
                    specFileList.innerHTML = html;
                    // Attach click handlers
                    var items = specFileList.querySelectorAll('.spec-file-item');
                    for (var j = 0; j < items.length; j++) {
                        items[j].addEventListener('click', function(e) {
                            var el = e.currentTarget;
                            var allItems = specFileList.querySelectorAll('.spec-file-item');
                            for (var k = 0; k < allItems.length; k++) allItems[k].classList.remove('selected');
                            el.classList.add('selected');
                            selectSpec(el.getAttribute('data-path'));
                        });
                    }
                })
                .catch(function(err) {
                    console.error('[spec-browser] Failed to load specs:', err);
                    specFileList.innerHTML = '<span class="spec-file-empty">Failed to load spec files</span>';
                });
        }

        function selectSpec(filePath) {
            console.log('[spec-browser] Selected:', filePath);
            if (specSelectedIndicator) specSelectedIndicator.style.display = '';
            if (specSelectedName) specSelectedName.textContent = filePath;
            if (specPathHidden) specPathHidden.value = filePath;
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        // Upload from machine → project artifacts
        if (specMachineUpload) {
            specMachineUpload.addEventListener('change', function(e) {
                var file = e.target.files[0];
                if (!file) return;
                console.log('[spec-browser] Upload from machine:', file.name, '(' + file.size + ' bytes)');
                if (specUploadStatus) { specUploadStatus.textContent = 'Uploading ' + file.name + '...'; specUploadStatus.style.color = ''; }
                if (typeof validateSpecContent === 'function') validateSpecContent(file);

                var qs = '?' + getProjectIdParam().replace(/^&/, '');
                var fd = new FormData();
                fd.append('file', file);
                fetch('api/upload-spec' + qs, { method: 'POST', body: fd })
                    .then(_checkResp).then(function(r) { return r.json(); })
                    .then(function(result) {
                        if (result.error) throw new Error(result.error);
                        console.log('[spec-browser] Upload success:', result.fileName, '→', result.path);
                        if (specUploadStatus) { specUploadStatus.textContent = 'Uploaded: ' + result.fileName; specUploadStatus.style.color = '#2e7d32'; }
                        selectSpec(result.path);
                        loadSpecs();
                    })
                    .catch(function(err) {
                        console.error('[spec-browser] Upload failed:', err.message);
                        if (specUploadStatus) { specUploadStatus.textContent = 'Upload failed: ' + err.message; specUploadStatus.style.color = '#ba1a1a'; }
                    });
            });
        }

        // Load specs on page init
        loadSpecs();

        // ── Toggle base URL and model name fields based on provider selection
        var OPENAI_DEFAULT_MODEL = 'kimi-k2-0905-preview';
        var ANTHROPIC_DEFAULT_MODEL = 'claude-sonnet-4-20250514';
        function toggleOpenAIFields() {
            const isOpenAI = providerSelect && providerSelect.value === 'openai';
            if (baseUrlField) {
                baseUrlField.style.display = isOpenAI ? 'flex' : 'none';
            }
            if (modelNameField) {
                modelNameField.style.display = 'flex';
            }
            var modelInput = document.getElementById('field-model');
            if (modelInput) {
                if (isOpenAI) {
                    if (!modelInput.value || modelInput.value === ANTHROPIC_DEFAULT_MODEL) {
                        modelInput.value = OPENAI_DEFAULT_MODEL;
                    }
                    modelInput.placeholder = OPENAI_DEFAULT_MODEL;
                } else {
                    if (!modelInput.value || modelInput.value === OPENAI_DEFAULT_MODEL) {
                        modelInput.value = ANTHROPIC_DEFAULT_MODEL;
                    }
                    modelInput.placeholder = ANTHROPIC_DEFAULT_MODEL;
                }
            }
        }

        if (providerSelect) {
            providerSelect.addEventListener('change', toggleOpenAIFields);
            toggleOpenAIFields();
        }

        // ── Spec validation helper ────────────────────────────────────
        window._specValid = true;
        function validateSpecContent(file) {
            var fd = new FormData();
            fd.append('spec_upload', file);
            var resultEl = document.getElementById('spec-validation-result');
            if (resultEl) resultEl.innerHTML = '<span style="color:var(--outline);font-size:0.8125rem;">Validating spec...</span>';
            fetch('validate-spec', { method: 'POST', body: fd })
                .then(_checkResp).then(function(r) { return r.text(); })
                .then(function(html) {
                    if (resultEl) resultEl.outerHTML = html;
                    window._specValid = html.indexOf('validation failed') === -1;
                })
                .catch(function() {
                    if (resultEl) resultEl.innerHTML = '';
                    window._specValid = true;
                });
        }

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

        // Block form submission when no spec is selected
        document.body.addEventListener('htmx:confirm', function(e) {
            var form = e.detail.elt;
            if (form.id !== 'main-form') return;
            var specPath = document.getElementById('field-spec_path');
            var specUpload = document.getElementById('spec-machine-upload');
            var hasSpec = (specPath && specPath.value.trim()) ||
                          (specUpload && specUpload.files && specUpload.files.length > 0);
            if (!hasSpec) {
                e.preventDefault();
                var msg = 'Please select or upload a spec file before generating documentation.';
                var existing = document.getElementById('spec-validation-msg');
                if (!existing) {
                    var indicator = document.getElementById('spec-selected-indicator');
                    if (indicator) {
                        var el = document.createElement('div');
                        el.id = 'spec-validation-msg';
                        el.style.cssText = 'color:#C20A29;font-size:13px;margin-top:6px;';
                        el.textContent = msg;
                        indicator.parentNode.insertBefore(el, indicator.nextSibling);
                    } else {
                        alert(msg);
                    }
                }
            } else {
                var existing = document.getElementById('spec-validation-msg');
                if (existing) existing.remove();
            }
        });

        // Poll job history — pause while an HTMX request targets the history panel
        var _htmxBusy = false;
        document.body.addEventListener('htmx:beforeRequest', function(e) {
            var tgt = e.detail && e.detail.target;
            if (tgt && tgt.id === 'job-history-content') _htmxBusy = true;
        });
        document.body.addEventListener('htmx:afterRequest', function(e) {
            var tgt = e.detail && e.detail.target;
            if (tgt && tgt.id === 'job-history-content') _htmxBusy = false;
        });
        setInterval(function() {
            if (_htmxBusy) return;
            var el = document.getElementById('job-history-content');
            if (!el) return;
            // Preserve <details> open state; auto-open when completed count changes
            var wasOpen = false;
            var prevCount = 0;
            var details = el.querySelector('details');
            if (details) {
                wasOpen = details.open;
                prevCount = details.querySelectorAll('tbody tr').length;
            }
            fetch('job-history')
                .then(_checkResp).then(function(r) { return r.text(); })
                .then(function(html) {
                    if (!_htmxBusy) {
                        el.innerHTML = html;
                        var d = el.querySelector('details');
                        if (d) {
                            var newCount = d.querySelectorAll('tbody tr').length;
                            if (wasOpen || newCount > prevCount) d.open = true;
                        }
                        if (window.htmx) htmx.process(el);
                    }
                })
                .catch(function() {});
        }, 10000);
    });
"""
