function appData() {
    return {
        // App State
        settingsOpen: false,
        appVersion: '',
        documentationOpen: false,
        documentationSection: 'readme',
        documentationHtml: '',
        documentationLoading: false,
        documentationError: '',
        documentationController: null,
        documentationPreviousOverflow: '',
        documentationTabs: [
            { id: 'readme', label: 'README' },
            { id: 'latest', label: 'Ostatnia wersja' },
            { id: 'instructions', label: 'Instrukcja' }
        ],
        loading: false,
        statusMsg: '',
        statusError: false,
        
        // SSE Event Source
        eventSource: null,
        logs: [],
        logsTaskId: '',
        jobs: [],
        currentJob: null,
        jobCounts: null,
        jobsError: '',
        jobsActionMessage: '',
        jobsActionError: '',
        cancellingJobs: '',
        cancelJobsController: null,
        fetchingJobs: false,
        jobsPollTimer: null,
        jobsController: null,
        jobDetailsController: null,
        activeJobId: localStorage.getItem('downloadeusz_active_job') || '',
        jobDetails: null,
        jobDetailsError: '',
        fetchingJobDetails: false,
        jobTrackOffset: 0,
        jobTrackLimit: 100,
        retryingJobId: null,
        destroyed: false,
        
        // Services & Integrations
        naviAccounts: [],
        selectedNaviAccount: localStorage.getItem('selectedNaviAccount') || '',
        slskdAccount: { configured: false, server: '', username: '' },
        naviSync: localStorage.getItem('naviSync') === 'true',
        statusDisplayMode: (localStorage.getItem('statusDisplayMode') && localStorage.getItem('statusDisplayMode') !== 'errors_only') ? localStorage.getItem('statusDisplayMode') : 'not_logged_or_error',
        naviStatus: { tested: false, ok: false, msg: '' },
        slskdStatus: { tested: false, ok: false, msg: '' },
        testingNavi: false,
        testingSlskd: false,
        organizingSlskd: false,
        beetsStatus: null,
        beetsStatusError: '',
        beetsImportError: '',
        beetsReviewItems: [],
        beetsReviewLimit: 20,
        beetsReviewError: '',
        fetchingBeetsReview: false,
        retryingBeetsReviewId: null,
        fetchingBeets: false,
        importingBeets: false,
        beetsPollTimer: null,
        normalizerStatus: null,
        normalizerError: '',
        normalizerActionError: '',
        normalizerActionMessage: '',
        fetchingNormalizer: false,
        submittingNormalizer: '',
        normalizerController: null,
        normalizerActionController: null,

        get normalizerRequests() {
            return (this.normalizerStatus?.requests || []).slice(0, 20);
        },

        get normalizerStatusText() {
            if (this.normalizerError) return 'Stan nieaktualny · brak połączenia';
            if (!this.normalizerStatus) return 'Sprawdzanie stanu…';
            if (!this.normalizerStatus.available) return 'Normalizeusz niedostępny';
            const counts = this.normalizerStatus.counts;
            return `Połączono · W kolejce: ${counts.pending} · W toku: ${counts.processing}`;
        },

        get normalizerAlbumPreview() {
            // The API returns newest first: a failed or pending newer preview invalidates the old one.
            return this.normalizerRequests.find(item => item.operation === 'preview_albums') || null;
        },

        get normalizerAlbumRepairReady() {
            const preview = this.normalizerAlbumPreview;
            if (preview?.status !== 'done' || !(preview.result?.changed > 0)) return false;
            const repair = this.normalizerRequests.find(item => item.operation === 'repair_albums');
            return !repair || (Number(preview.created_at) > Number(repair.created_at));
        },

        canRunNormalizer(operation) {
            if (this.destroyed || this.normalizerError || this.submittingNormalizer
                || !this.normalizerStatus?.available
                || !this.normalizerStatus.capabilities.includes(operation)) return false;
            if (this.normalizerRequests.some(item => item.operation === operation
                && ['pending', 'processing'].includes(item.status))) return false;
            return operation !== 'repair_albums' || this.normalizerAlbumRepairReady;
        },

        normalizerOperationText(operation) {
            return { preview_albums: 'Sprawdzenie brakujących albumów',
                repair_albums: 'Uzupełnianie pustych albumów',
                scan_library: 'Sprawdzenie ReplayGain biblioteki',
                normalize: 'ReplayGain plików po imporcie' }[operation] || 'Zadanie Normalizeusza';
        },

        normalizerRequestStatus(request) {
            return { pending: 'W kolejce', processing: 'W toku', done: 'Zakończone', failed: 'Błąd' }[request.status] || 'Nieznany stan';
        },

        normalizerProgressText(request) {
            const progress = request.result || request.progress;
            if (!progress) return request.status === 'pending'
                ? 'Czeka na rozpoczęcie przez Normalizeusza.' : 'Oczekiwanie na dane postępu.';
            const count = value => Number.isFinite(value) && value >= 0 ? value : 0;
            const total = Number.isFinite(progress.total) && progress.total > 0 ? ` z ${progress.total}` : '';
            if (request.operation === 'scan_library') return `Sprawdzone: ${count(progress.processed)}${total} · Błędy: ${count(progress.errors)}`;
            const changed = request.operation === 'preview_albums' ? 'Do uzupełnienia' : 'Zmienione';
            return `Sprawdzone: ${count(progress.processed)}${total} · ${changed}: ${count(progress.changed)} · Pominięte: ${count(progress.skipped)} · Błędy: ${count(progress.errors)}`;
        },

        normalizerSamples(request, errors = false) {
            const samples = request.result?.[errors ? 'error_samples' : 'samples'];
            return Array.isArray(samples) ? samples.slice(0, 5) : [];
        },

        get beetsStatusText() {
            if (this.beetsStatusError) return this.beetsStatusError;
            if (!this.beetsStatus) return 'Sprawdzanie stanu…';
            if (!this.beetsStatus.enabled) return 'Wyłączony';
            if (!this.beetsStatus.available) return 'Niedostępny na serwerze';
            if (this.beetsStatus.running) return 'Porządkowanie w toku…';
            if (this.beetsStatus.last_error) return 'Ostatni import wymaga sprawdzenia';
            return 'Gotowy do importu';
        },

        get beetsScheduleText() {
            if (!this.beetsStatus?.enabled || !this.beetsStatus.available) return '';
            const seconds = Number(this.beetsStatus.auto_interval);
            if (!Number.isFinite(seconds) || seconds <= 0) return 'Import automatyczny wyłączony';
            return seconds % 60 === 0
                ? `Sprawdzanie nowych plików co ${seconds / 60} min`
                : `Sprawdzanie nowych plików co ${seconds} s`;
        },

        get canImportBeets() {
            return Boolean(this.beetsStatus?.enabled && this.beetsStatus.available
                && !this.beetsStatus.running && !this.fetchingBeets && !this.importingBeets
                && !this.loading && !this.organizingSlskd);
        },

        get beetsReviewSummary() {
            if (this.beetsReviewError) return 'Do sprawdzenia · lista niedostępna';
            if (this.fetchingBeetsReview) return 'Do sprawdzenia · wczytywanie…';
            return `Do sprawdzenia (${this.beetsReviewItems.length})`;
        },

        beetsReviewFilename(item) {
            return String(item.source || '').split(/[\\/]/).pop() || 'Nieznany plik';
        },

        beetsReviewStatus(item) {
            if (item.source_changed) return 'Źródło zmienione, dotychczasowa kopia zachowana';
            if (item.imported && item.metadata_confirmed && item.release_confirmed && item.artwork_pending) return 'Nagranie rozpoznane, okładka do uzupełnienia';
            if (item.metadata_confirmed && !item.release_confirmed) return 'Nagranie rozpoznane, wydanie niepotwierdzone';
            return item.imported ? 'Dodany według tagów' : 'Czeka na import';
        },

        beetsCandidateText(candidate) {
            const song = [candidate.artist, candidate.title].filter(Boolean).join(' — ');
            const album = [candidate.album, candidate.year].filter(Boolean).join(' · ');
            return [song, album].filter(Boolean).join(' / ') || 'Propozycja bez opisu';
        },

        // Spotify Metadata
        spotifyMetadata: null,
        fetchingMeta: false,
        copiedMetaCommand: false,
        copiedMetaFeedbackTimer: null,

        get selectedNaviUsername() {
            if (!this.selectedNaviAccount) return '';
            const acc = this.naviAccounts.find(a => a.id === this.selectedNaviAccount);
            return acc ? acc.username : this.selectedNaviAccount;
        },

        // Input Data
        url: '',
        selectedTool: 'spotdl', // Default to spotdl now as it has more features
        flags: {
            skip_downloaded: true,
            audio_only: false,
            // SpotDL specific flags
            use_cookies: true,
            dont_filter: true,
            audio_sources: true,
            save_errors: true,
            yt_dlp_args: true,
            threads: 1,
            generate_m3u: false,
            public_playlist: false,
            preferred_format: 'flac',
            youtube_fallback: false,
            m3u_name: '', // empty means {list}
            check_navidrome: false,
            navidrome_skip_mode: 'flac_only'
        },
        
        // Settings / Appearance
        showTitle: true,
        themeColor: '#3b82f6', // default blue or 'auto'
        extractedColor: '#3b82f6', // used when themeColor is 'auto'
        availableColors: ['#3b82f6', '#10b981', '#8b5cf6', '#ef4444', '#f59e0b', '#ec4899', '#6b7280', '#ffffff', '#14b8a6', '#0ea5e9', '#6366f1', '#a855f7', '#d946ef', '#f43f5e', '#eab308'],
        autoColor: false, // legacy, keeping for compatibility
        
        bgType: 'preset', // 'preset' or 'image'
        bgPreset: 'dark', 
        
        blurAmount: 0,
        whiteGradient: 0,
        
        presetColors: {
            'dark': '#171717',
            'slate': '#334155',
            'ocean': '#0284c7',
            'indigo': '#4f46e5',
            'purple': '#7e22ce',
            'pink': '#be185d',
            'rose': '#e11d48',
            'sunset': '#c2410c',
            'orange': '#ea580c',
            'forest': '#15803d',
            'teal': '#0f766e',
            'cyan': '#0e7490',
        },
        presetAccentColors: {
            'dark': '#3b82f6',
            'slate': '#38bdf8',
            'ocean': '#38bdf8',
            'indigo': '#6366f1',
            'purple': '#a855f7',
            'pink': '#ec4899',
            'rose': '#f43f5e',
            'sunset': '#f97316',
            'orange': '#fb923c',
            'forest': '#22c55e',
            'teal': '#14b8a6',
            'cyan': '#06b6d4',
        },
        
        wallpapers: [],
        selectedImage: '',
        
        errorsList: [],
        errorsOpen: false,
        
        logMode: 'advanced', // changed default to advanced since user wants terminal

        initApp() {
            this.loadSettings();
            this.fetchVersion();
            this.fetchAccounts();
            this.fetchWallpapers();
            this.fetchErrors();
            this.fetchBeetsStatus();
            this.fetchJobs();
            this.fetchNormalizer();
            this.jobsPollTimer = setInterval(() => {
                this.fetchJobs();
                if (!document.hidden) this.fetchNormalizer();
            }, 5000);
            this.beetsPollTimer = setInterval(() => {
                if (!document.hidden) this.fetchBeetsStatus();
            }, 15000);
            
            // Watch for changes and save to local storage
            this.$watch('showTitle', () => this.saveSettings());
            this.$watch('themeColor', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('bgType', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('bgPreset', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('selectedImage', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('blurAmount', () => this.saveSettings());
            this.$watch('whiteGradient', () => this.saveSettings());
            this.$watch('logMode', () => this.saveSettings());
            this.$watch('statusDisplayMode', value => {
                localStorage.setItem('statusDisplayMode', value);
                this.saveSettings();
            });
            this.$watch('flags', () => this.saveSettings(), { deep: true });
            
            // Auto-detect tool based on URL (Spotify -> spotdl, YouTube/SoundCloud -> ytdl)
            this.$watch('url', value => {
                if (!value || this.selectedTool === 'soulseek') return;
                const lower = value.trim().toLowerCase();
                if (lower.includes('spotify.com') || lower.includes('open.spotify.com')) {
                    this.selectedTool = 'spotdl';
                } else if (
                    lower.includes('youtube.com') ||
                    lower.includes('youtu.be') ||
                    lower.includes('music.youtube.com') ||
                    lower.includes('soundcloud.com') ||
                    lower.includes('on.soundcloud.com')
                ) {
                    this.selectedTool = 'ytdl';
                }
            });
            
            // Watcher for navidrome account selection
            this.$watch('selectedNaviAccount', value => {
                localStorage.setItem('selectedNaviAccount', value || '');
                this.naviStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('naviSync', value => {
                localStorage.setItem('naviSync', value ? 'true' : 'false');
                this.saveSettings();
            });

            this.$nextTick(() => {
                this.updateExtractedColor();
            });
        },

        destroy() {
            this.destroyed = true;
            clearInterval(this.beetsPollTimer);
            clearInterval(this.jobsPollTimer);
            clearTimeout(this.copiedMetaFeedbackTimer);
            this.jobsController?.abort();
            this.cancelJobsController?.abort();
            this.jobDetailsController?.abort();
            this.documentationController?.abort();
            this.normalizerController?.abort();
            this.normalizerActionController?.abort();
            this.eventSource?.close();
        },

        async fetchVersion() {
            try {
                const response = await fetch('/api/version');
                if (!response.ok) return;
                const data = await response.json();
                this.appVersion = data.version || '';
            } catch (_) {
                // The documentation request also provides the version when available.
            }
        },

        openDocumentation() {
            this.settingsOpen = false;
            this.errorsOpen = false;
            this.documentationPreviousOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            this.$refs.documentationDialog.showModal();
            this.documentationOpen = true;
            this.loadDocumentation(this.documentationSection);
        },

        closeDocumentation() {
            this.$refs.documentationDialog.close();
        },

        documentationClosed() {
            this.documentationOpen = false;
            this.documentationController?.abort();
            document.body.style.overflow = this.documentationPreviousOverflow;
            this.$refs.documentationTrigger.focus();
        },

        async loadDocumentation(section) {
            if (!this.documentationTabs.some(tab => tab.id === section)) return;
            this.documentationController?.abort();
            const controller = new AbortController();
            this.documentationController = controller;
            this.documentationSection = section;
            this.documentationHtml = '';
            this.documentationError = '';
            this.documentationLoading = true;
            this.$refs.documentationPanel.scrollTop = 0;
            try {
                const response = await fetch(`/api/documentation/${section}`, { signal: controller.signal });
                if (!response.ok) throw new Error('Nie udało się wczytać dokumentacji. Spróbuj ponownie.');
                const data = await response.json();
                if (controller !== this.documentationController || controller.signal.aborted) return;
                if (typeof data.html !== 'string' || !data.html.trim()) throw new Error('Ten dokument jest pusty. Spróbuj ponownie po aktualizacji aplikacji.');
                this.documentationHtml = data.html;
                this.appVersion = data.version || this.appVersion;
            } catch (error) {
                if (controller === this.documentationController && !controller.signal.aborted) {
                    this.documentationError = error.message || 'Nie udało się wczytać dokumentacji. Spróbuj ponownie.';
                }
            } finally {
                if (controller === this.documentationController) this.documentationLoading = false;
            }
        },

        documentationTabKeydown(event) {
            const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
            if (!keys.includes(event.key)) return;
            event.preventDefault();
            const tabs = this.documentationTabs;
            const current = tabs.findIndex(tab => tab.id === this.documentationSection);
            const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
                : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            this.loadDocumentation(tabs[index].id);
            this.$nextTick(() => document.getElementById(`documentation-tab-${tabs[index].id}`).focus());
        },
        
        toggleSettings() {
            this.settingsOpen = !this.settingsOpen;
        },
        
        saveSettings() {
            const settings = {
                showTitle: this.showTitle,
                themeColor: this.themeColor,
                bgType: this.bgType,
                bgPreset: this.bgPreset,
                selectedImage: this.selectedImage,
                blurAmount: this.blurAmount,
                whiteGradient: this.whiteGradient,
                logMode: this.logMode,
                statusDisplayMode: this.statusDisplayMode,
                flags: this.flags
            };
            localStorage.setItem('downloadeusz_settings', JSON.stringify(settings));
            localStorage.setItem('statusDisplayMode', this.statusDisplayMode);
            localStorage.setItem('selectedNaviAccount', this.selectedNaviAccount || '');
            localStorage.setItem('naviSync', this.naviSync ? 'true' : 'false');
        },
        
        loadSettings() {
            const saved = localStorage.getItem('downloadeusz_settings');
            if (saved) {
                try {
                    const settings = JSON.parse(saved);
                    this.showTitle = settings.showTitle ?? true;
                    this.themeColor = settings.themeColor || '#3b82f6';
                    this.bgType = settings.bgType || 'preset';
                    this.bgPreset = settings.bgPreset || 'dark';
                    this.selectedImage = settings.selectedImage || '';
                    this.blurAmount = settings.blurAmount || 0;
                    this.whiteGradient = settings.whiteGradient || 0;
                    this.logMode = settings.logMode || 'advanced';
                    this.statusDisplayMode = settings.statusDisplayMode || localStorage.getItem('statusDisplayMode') || 'not_logged_or_error';
                    if (this.statusDisplayMode === 'errors_only') {
                        this.statusDisplayMode = 'not_logged_or_error';
                    }
                    
                    if (settings.flags) {
                        this.flags = {
                            ...this.flags,
                            ...settings.flags,
                            check_navidrome: settings.flags.check_navidrome ?? false,
                            navidrome_skip_mode: (settings.flags.navidrome_skip_mode === 'all') ? 'all' : 'flac_only',
                            preferred_format: settings.flags.preferred_format === 'mp3' ? 'mp3' : 'flac',
                            youtube_fallback: settings.flags.youtube_fallback === true
                        };
                    } else {
                        this.flags.navidrome_skip_mode = 'flac_only';
                    }
                } catch (e) {}
            }
            if (!this.statusDisplayMode || this.statusDisplayMode === 'errors_only') {
                const storedMode = localStorage.getItem('statusDisplayMode');
                this.statusDisplayMode = (storedMode && storedMode !== 'errors_only') ? storedMode : 'not_logged_or_error';
            }

            // Remove obsolete plaintext credentials from localStorage
            localStorage.removeItem('slskdUrl');
            localStorage.removeItem('slskdUser');
            localStorage.removeItem('slskdPass');
            localStorage.removeItem('naviUrl');
            localStorage.removeItem('naviUser');
            localStorage.removeItem('naviPass');

            this.selectedNaviAccount = localStorage.getItem('selectedNaviAccount') || '';
            if (localStorage.getItem('naviSync') !== null) {
                this.naviSync = localStorage.getItem('naviSync') === 'true';
            }
        },
        
        async fetchWallpapers() {
            try {
                const res = await fetch('/api/wallpapers');
                const data = await res.json();
                if (data.wallpapers) {
                    this.wallpapers = data.wallpapers;
                    if (!this.selectedImage && this.wallpapers.length > 0) {
                        this.selectedImage = this.wallpapers[0];
                    }
                }
            } catch (error) {
                console.error('Failed to load wallpapers', error);
            }
        },

        getBackgroundStyle() {
            if (this.bgType === 'image' && this.selectedImage) {
                return `background-image: url('/wallpapers/${encodeURI(this.selectedImage)}')`;
            }
            if (this.bgType === 'preset') {
                let baseColor = this.presetColors[this.bgPreset] || '#171717';
                let whiteOp = this.whiteGradient / 100;
                return `background-image: linear-gradient(135deg, ${baseColor}, rgba(255, 255, 255, ${whiteOp}))`;
            }
            return 'background-color: #171717'; // default dark
        },
        
        isTooDark(hex) {
            if (!hex || typeof hex !== 'string' || !hex.startsWith('#')) return false;
            let clean = hex.slice(1);
            if (clean.length === 3) {
                clean = clean.split('').map(c => c + c).join('');
            }
            if (clean.length !== 6) return false;
            const r = parseInt(clean.slice(0, 2), 16);
            const g = parseInt(clean.slice(2, 4), 16);
            const b = parseInt(clean.slice(4, 6), 16);
            if (isNaN(r) || isNaN(g) || isNaN(b)) return false;
            const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            return lum < 0.32; // Boost/fallback if brightness is below 32%
        },

        getEffectiveColor() {
            let color = this.themeColor;
            if (color === 'auto') {
                color = this.extractedColor;
            }
            if (!color || color === 'auto' || this.isTooDark(color)) {
                return '#3b82f6';
            }
            return color;
        },
        
        rgbToHex(r, g, b) {
            return "#" + (1 << 24 | r << 16 | g << 8 | b).toString(16).slice(1);
        },
        
        updateExtractedColor() {
            if (this.themeColor !== 'auto') return;
            
            if (this.bgType === 'preset') {
                this.extractedColor = this.presetAccentColors[this.bgPreset] || '#3b82f6';
            } else if (this.bgType === 'image' && this.selectedImage) {
                // Use ColorThief to extract vibrant accent color
                const img = new Image();
                img.crossOrigin = 'Anonymous';
                img.onload = () => {
                    try {
                        const colorThief = new ColorThief();
                        const palette = colorThief.getPalette(img, 8);
                        let bestColor = null;
                        if (palette && palette.length > 0) {
                            for (const c of palette) {
                                const hex = this.rgbToHex(c[0], c[1], c[2]);
                                if (!this.isTooDark(hex)) {
                                    bestColor = hex;
                                    break;
                                }
                            }
                        }
                        if (!bestColor) {
                            const dom = colorThief.getColor(img);
                            const hex = this.rgbToHex(dom[0], dom[1], dom[2]);
                            bestColor = !this.isTooDark(hex) ? hex : '#3b82f6';
                        }
                        this.extractedColor = bestColor;
                    } catch (e) {
                        console.error("ColorThief failed", e);
                        this.extractedColor = '#3b82f6';
                    }
                };
                img.src = `/wallpapers/${encodeURI(this.selectedImage)}`;
            }
        },
        
        getCommandPreview() {
            let cmd = this.selectedTool === 'spotdl' ? 'spotdl ' : 'yt-dlp ';
            cmd += `"${this.url || 'LINK'}" `;
            
            if (this.selectedTool === 'spotdl') {
                cmd += `--output "{artist}/{year} - {album}/{track-number} {title}.{ext}" `;
                if (this.flags.generate_m3u) {
                    let mName = this.flags.m3u_name.trim() || "{list}";
                    cmd += `--m3u "${mName}.m3u" `;
                }
                if (this.flags.use_cookies) cmd += `--cookie-file ~/spotdl/youtube_cookies.txt `;
                if (this.flags.skip_downloaded) cmd += `--overwrite skip `;
                if (this.flags.dont_filter) cmd += `--dont-filter-results `;
                if (this.flags.audio_sources) cmd += `--audio youtube-music youtube soundcloud `;
                if (this.flags.threads) cmd += `--threads ${this.flags.threads} `;
                if (this.flags.save_errors) cmd += `--save-errors ~/spotdl/nadal_brakujace.txt `;
                if (this.flags.yt_dlp_args) cmd += `--yt-dlp-args "--sleep-interval 2 --max-sleep-interval 4" `;
            } else {
                if (this.flags.audio_only) cmd += `-x --audio-format mp3 `;
                if (this.flags.skip_downloaded) cmd += `--no-overwrites `;
            }
            return cmd.trim();
        },
        
        getTextColor(defaultColor = '#ffffff') {
            return this.getEffectiveColor() === '#ffffff' ? '#374151' : defaultColor;
        },
        
        async fetchErrors() {
            try {
                const res = await fetch('/api/errors');
                const data = await res.json();
                if (data.errors) {
                    this.errorsList = data.errors;
                }
            } catch (error) {
                console.error('Failed to load errors', error);
            }
        },
        
        get currentTaskTrackText() {
            const track = this.currentJob?.current_track;
            return track ? [track.artist, track.title].filter(Boolean).join(' — ') : '';
        },

        get currentTaskTrackPosition() {
            const job = this.currentJob;
            if (!Number.isInteger(job?.current_track?.position)) return '';
            return `Utwór ${job.current_track.position + 1} z ${job.tracks_total}`;
        },

        get currentTaskSearch() {
            return this.currentJob?.status === 'running' && this.currentJob.stage === 'searching'
                ? this.currentJob.search : null;
        },

        get currentTaskProgress() {
            const job = this.currentJob;
            if (!job?.tracks_total) return '';
            const counts = job.track_counts || {};
            const completed = ['imported', 'existing', 'done', 'skipped'].reduce((sum, status) => sum + (counts[status] || 0), 0);
            return `Zakończone utwory: ${completed} z ${job.tracks_total}`;
        },

        get currentTaskIssues() {
            const counts = this.currentJob?.track_counts || {};
            return [counts.failed ? `Błędy: ${counts.failed}` : '',
                counts.needs_review ? `Do sprawdzenia: ${counts.needs_review}` : ''].filter(Boolean).join(' · ');
        },

        get currentTaskRetryText() {
            const job = this.currentJob;
            if (job?.status !== 'waiting' || !(job.next_attempt * 1000 > Date.now())) return '';
            const time = new Date(job.next_attempt * 1000).toLocaleTimeString('pl-PL', {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            return `Następne sprawdzenie: ${time}`;
        },

        async showCurrentJobDetails() {
            if (!this.currentJob) return;
            await this.selectJob(this.currentJob.id);
            this.$refs.queueHeading.scrollIntoView({ block: 'start' });
            this.$refs.queueHeading.focus({ preventScroll: true });
        },

        get queueSummary() {
            if (!this.jobCounts) return this.jobsError ? 'Stan chwilowo niedostępny' : 'Wczytywanie zadań…';
            const counts = this.jobCounts;
            const active = (counts.queued || 0) + (counts.running || 0) + (counts.waiting || 0);
            const review = (counts.failed || 0) + (counts.needs_review || 0);
            return `Aktywne: ${active} · Do sprawdzenia: ${review}`;
        },

        jobTitle(job) {
            return job.title || ({ import: 'Import muzyki', organize: 'Porządkowanie plików Soulseek',
                soulseek: 'Pobieranie z Soulseek' }[job.kind]) || 'Pobieranie muzyki';
        },

        jobIsActive(job) {
            return ['queued', 'running', 'waiting'].includes(job.status);
        },

        jobCanRetry(job) {
            return ['failed', 'needs_review', 'cancelled'].includes(job.status);
        },

        get canCancelJobs() {
            if (this.destroyed || this.cancellingJobs || this.retryingJobId) return false;
            const counts = this.jobCounts || {};
            return (counts.queued || 0) + (counts.running || 0) + (counts.waiting || 0) > 0
                || this.jobs.some(job => this.jobIsActive(job));
        },

        jobStatusText(job) {
            const stage = { queued: 'Przygotowanie zadania', resolving: 'Odczytywanie listy utworów',
                checking: 'Sprawdzanie biblioteki', downloading: 'Pobieranie', searching: 'Wyszukiwanie w Soulseek',
                transferring: 'Pobieranie z Soulseek',
                organizing: 'Porządkowanie plików Soulseek', finalizing: 'Kończenie zadania',
                importing: 'Importowanie do biblioteki', normalizing: 'Wyrównywanie głośności · ReplayGain',
                scanning: 'Skanowanie Navidrome', playlist: 'Tworzenie playlisty w Navidrome', done: 'Gotowe' }[job.stage] || 'Przetwarzanie';
            if (job.status === 'queued') return 'W kolejce';
            if (job.status === 'done') return 'Gotowe';
            if (job.status === 'cancelled') return 'Zatrzymano';
            if (job.status === 'failed') return job.stage === 'done' ? 'Błąd' : `Błąd · ${stage}`;
            if (job.status === 'needs_review') return job.stage === 'done' ? 'Do sprawdzenia' : `Do sprawdzenia · ${stage}`;
            return job.status === 'waiting' ? `Oczekiwanie · ${stage}` : stage;
        },

        jobTime(job) {
            const date = new Date(Number(job.created) * 1000);
            return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('pl-PL', {
                day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
            });
        },

        trackStatusText(track) {
            return { queued: 'W kolejce', running: 'W toku', waiting: 'Oczekiwanie', downloading: 'Pobieranie',
                retry: 'Oczekiwanie na ponowienie',
                transferring: 'Pobieranie z Soulseek', downloaded: 'Pobrano', imported: 'W bibliotece',
                existing: 'Już w bibliotece', done: 'Gotowe', skipped: 'Pominięto',
                failed: 'Błąd', needs_review: 'Do sprawdzenia', cancelled: 'Zatrzymano' }[track.status] || 'W toku';
        },

        get jobTracksTotal() {
            return this.jobDetails?.tracks_total ?? this.jobDetails?.tracks?.length ?? 0;
        },

        get visibleJobTracks() {
            const tracks = this.jobDetails?.tracks || [];
            const offset = this.jobDetails?.tracks_offset === undefined ? this.jobTrackOffset : 0;
            return tracks.slice(offset, offset + this.jobTrackLimit);
        },

        get jobTrackRange() {
            const offset = this.jobDetails?.tracks_offset ?? this.jobTrackOffset;
            const length = this.visibleJobTracks.length;
            return `Utwory ${length ? offset + 1 : 0}–${offset + length} z ${this.jobTracksTotal}`;
        },

        async changeJobTrackPage(direction) {
            if (this.fetchingJobDetails || !this.activeJobId) return;
            const offset = this.jobTrackOffset + direction * this.jobTrackLimit;
            if (offset < 0 || offset >= this.jobTracksTotal) return;
            this.jobTrackOffset = offset;
            await this.selectJob(this.activeJobId);
        },

        async fetchJobs() {
            if (this.fetchingJobs || this.destroyed) return;
            this.fetchingJobs = true;
            const controller = new AbortController();
            this.jobsController = controller;
            try {
                const response = await fetch('/api/jobs', { signal: controller.signal });
                if (!response.ok) throw new Error('Queue unavailable');
                const data = await response.json();
                if (!Array.isArray(data.items)) throw new Error('Invalid queue');
                if (this.destroyed || controller.signal.aborted) return;
                this.jobs = data.items;
                this.currentJob = data.current_job || null;
                this.jobCounts = data.counts || {};
                this.jobsError = '';
                const selected = this.activeJobId || this.jobs.find(job => this.jobIsActive(job))?.id || this.jobs[0]?.id;
                if (selected) await this.selectJob(selected);
            } catch (error) {
                if (error.name !== 'AbortError' && !this.destroyed && !controller.signal.aborted) {
                    this.currentJob = null;
                    this.jobsError = 'Nie udało się odświeżyć kolejki. Ponowimy próbę za chwilę.';
                }
            } finally {
                if (this.jobsController === controller) this.fetchingJobs = false;
            }
        },

        async selectJob(taskId) {
            if (this.destroyed) return;
            const changed = this.activeJobId !== taskId;
            this.activeJobId = taskId;
            localStorage.setItem('downloadeusz_active_job', taskId);
            if (changed) {
                this.jobTrackOffset = 0;
                this.jobDetails = null;
                this.jobDetailsError = '';
                this.eventSource?.close();
                this.eventSource = null;
                this.logsTaskId = '';
                this.logs = [];
            }
            this.jobDetailsController?.abort();
            const controller = new AbortController();
            this.jobDetailsController = controller;
            this.fetchingJobDetails = true;
            try {
                const response = await fetch(`/api/jobs/${encodeURIComponent(taskId)}?offset=${this.jobTrackOffset}&limit=${this.jobTrackLimit}`, { signal: controller.signal });
                if (!response.ok) {
                    if (response.status === 404 && this.activeJobId === taskId) {
                        this.activeJobId = '';
                        localStorage.removeItem('downloadeusz_active_job');
                    }
                    throw new Error('Job unavailable');
                }
                const job = await response.json();
                if (this.destroyed || controller.signal.aborted || this.activeJobId !== taskId) return;
                const previousStatus = this.jobDetails?.status;
                this.jobDetails = job;
                this.jobDetailsError = '';
                this.statusMsg = `${this.jobTitle(job)} · ${this.jobStatusText(job)}`;
                this.statusError = ['failed', 'needs_review'].includes(job.status);
                if (this.jobIsActive(job)) {
                    if (this.logsTaskId !== taskId) this.connectLogs(taskId);
                } else {
                    if (this.logsTaskId === taskId) this.eventSource?.close();
                    if (previousStatus !== job.status) {
                        this.fetchBeetsStatus();
                        this.fetchErrors();
                    }
                }
            } catch (error) {
                if (error.name !== 'AbortError' && !this.destroyed && this.activeJobId === taskId) {
                    this.jobDetailsError = 'Nie udało się wczytać postępu. Ponowimy próbę za chwilę.';
                }
            } finally {
                if (this.jobDetailsController === controller) this.fetchingJobDetails = false;
            }
        },

        async cancelJobs(job = null) {
            if (this.destroyed || this.cancellingJobs || this.retryingJobId || (job && !this.jobIsActive(job))) return;
            this.cancellingJobs = job ? job.id : '*';
            this.jobsActionMessage = '';
            this.jobsActionError = '';
            const controller = new AbortController();
            this.cancelJobsController = controller;
            try {
                const url = job ? `/api/jobs/${encodeURIComponent(job.id)}/cancel` : '/api/jobs/cancel';
                const response = await fetch(url, { method: 'POST', signal: controller.signal });
                const data = await response.json();
                if (!response.ok || data.status !== 'cancelled') {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Nie udało się zatrzymać zadania. Spróbuj ponownie.');
                }
                if (this.destroyed) return;
                this.jobsController?.abort();
                this.jobDetailsController?.abort();
                this.fetchingJobs = false;
                const affected = item => item && (!job || item.id === job.id) && this.jobIsActive(item);
                if (affected(this.currentJob)) this.currentJob = null;
                const selected = affected(this.jobDetails);
                for (const item of [...this.jobs, this.jobDetails]) {
                    if (affected(item)) {
                        item.status = 'cancelled';
                        item.error = '';
                    }
                }
                if (!job || this.logsTaskId === job.id) {
                    this.eventSource?.close();
                    this.eventSource = null;
                    this.logsTaskId = '';
                }
                if (selected) {
                    this.statusMsg = `${this.jobTitle(this.jobDetails)} · Zatrzymano`;
                    this.statusError = false;
                }
                this.jobsActionMessage = data.message || (job ? 'Zatrzymano zadanie. Pliki pozostają na dysku.'
                    : `Zatrzymano zadania: ${data.cancelled}. Pliki pozostają na dysku.`);
                await this.fetchJobs();
            } catch (error) {
                if (!this.destroyed && error.name !== 'AbortError') {
                    this.jobsActionError = error instanceof TypeError
                        ? 'Nie udało się połączyć z serwerem. Spróbuj ponownie.' : error.message;
                }
            } finally {
                this.cancellingJobs = '';
            }
        },

        async retryJob(job) {
            if (!this.jobCanRetry(job) || this.retryingJobId || this.cancellingJobs || this.destroyed) return;
            this.jobsActionMessage = '';
            this.jobsActionError = '';
            this.retryingJobId = job.id;
            this.jobsError = '';
            try {
                const response = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/retry`, { method: 'POST' });
                const data = await response.json();
                if (!response.ok || !data.task_id) {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Nie udało się ponowić zadania.');
                }
                if (this.destroyed) return;
                job.status = data.status || 'queued';
                job.error = '';
                this.connectLogs(data.task_id);
                await this.selectJob(data.task_id);
                this.fetchJobs();
            } catch (error) {
                if (!this.destroyed) this.jobsError = error instanceof TypeError
                    ? 'Nie udało się połączyć z serwerem. Spróbuj ponownie.' : error.message;
            } finally {
                this.retryingJobId = null;
            }
        },

        connectLogs(taskId) {
            if (this.destroyed) return;
            if (this.activeJobId !== taskId) this.jobTrackOffset = 0;
            if (this.eventSource) {
                this.eventSource.close();
            }
            
            this.logs = [];
            if (this.jobDetails?.id !== taskId) this.jobDetails = null;
            this.jobDetailsError = '';
            this.logsTaskId = taskId;
            this.activeJobId = taskId;
            localStorage.setItem('downloadeusz_active_job', taskId);
            this.eventSource = new EventSource(`/api/logs/${taskId}`);
            const source = this.eventSource;
            
            this.eventSource.onmessage = (event) => {
                if (this.destroyed || this.eventSource !== source) return;
                this.logs.push(event.data);
                
                // Auto scroll to bottom
                this.$nextTick(() => {
                    const container = document.getElementById('log-container');
                    if (container) {
                        container.scrollTop = container.scrollHeight;
                    }
                });
                
                // Download log markers can precede import, ReplayGain and the Navidrome scan.
            };
            
            this.eventSource.onerror = () => {
                if (this.eventSource !== source || this.destroyed) return;
                const message = 'Utracono połączenie z logami. Postęp zadania jest nadal widoczny w kolejce.';
                if (this.logs[this.logs.length - 1] !== message) this.logs.push(message);
            };
        },
        
        async fetchAccounts() {
            try {
                const [naviRes, slskdRes] = await Promise.all([
                    fetch('/api/accounts/navidrome'),
                    fetch('/api/accounts/slskd')
                ]);
                if (naviRes.ok) {
                    const naviData = await naviRes.json();
                    this.naviAccounts = naviData.accounts || [];
                    if (this.naviAccounts.length > 0) {
                        const exists = this.naviAccounts.some(a => a.id === this.selectedNaviAccount);
                        if (!exists) {
                            this.selectedNaviAccount = this.naviAccounts[0].id;
                            localStorage.setItem('selectedNaviAccount', this.selectedNaviAccount);
                        }
                    } else {
                        this.selectedNaviAccount = '';
                        localStorage.removeItem('selectedNaviAccount');
                    }
                }
                if (slskdRes.ok) {
                    const slskdData = await slskdRes.json();
                    this.slskdAccount = slskdData || { configured: false, server: '', username: '' };
                }
            } catch (error) {
                console.error('Failed to fetch accounts', error);
            } finally {
                this.checkServicesStatus();
                this.fetchBeetsStatus();
            }
        },

        async fetchNormalizer(force = false) {
            if (this.destroyed || (this.fetchingNormalizer && !force)) return;
            if (force) this.normalizerController?.abort();
            const controller = new AbortController();
            this.normalizerController = controller;
            this.fetchingNormalizer = true;
            try {
                const response = await fetch('/api/normalizer', { signal: controller.signal });
                if (!response.ok) throw new Error('Status unavailable');
                const data = await response.json();
                if (!data || typeof data.available !== 'boolean' || typeof data.enabled !== 'boolean'
                    || !Array.isArray(data.capabilities) || !Array.isArray(data.requests)
                    || !Number.isInteger(data.counts?.pending) || !Number.isInteger(data.counts?.processing)) {
                    throw new Error('Invalid status');
                }
                if (this.destroyed || controller.signal.aborted) return;
                data.requests = data.requests.filter(item => item && typeof item.id === 'string').slice(0, 20);
                this.normalizerStatus = data;
                this.normalizerError = '';
            } catch (error) {
                if (!this.destroyed && !controller.signal.aborted) {
                    this.normalizerError = 'Nie udało się odświeżyć stanu Normalizeusza. Odśwież, aby spróbować ponownie. Wyświetlone zadania mogą być nieaktualne.';
                }
            } finally {
                if (this.normalizerController === controller) {
                    this.fetchingNormalizer = false;
                    this.normalizerController = null;
                }
            }
        },

        async runNormalizer(operation) {
            if (!this.canRunNormalizer(operation)) return;
            this.submittingNormalizer = operation;
            this.normalizerActionError = '';
            this.normalizerActionMessage = '';
            const controller = new AbortController();
            this.normalizerActionController = controller;
            try {
                const response = await fetch('/api/normalizer/jobs', {
                    method: 'POST', signal: controller.signal,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ operation, navi_account_id: this.selectedNaviAccount || '' })
                });
                const data = await response.json();
                if (this.destroyed || controller.signal.aborted) return;
                if (!response.ok || typeof data.id !== 'string' || data.status !== 'pending') {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Nie udało się dodać zadania. Odśwież stan i spróbuj ponownie.');
                }
                this.normalizerActionMessage = data.warning || 'Dodano zadanie do kolejki Normalizeusza.';
                this.normalizerStatus.requests.unshift({ id: data.id, operation, status: 'pending', created_at: Date.now() / 1000 });
                await this.fetchNormalizer(true);
            } catch (error) {
                if (!this.destroyed && !controller.signal.aborted) {
                    this.normalizerActionError = error instanceof TypeError
                        ? 'Nie udało się połączyć z serwerem. Odśwież stan przed ponowieniem zadania.'
                        : error.message || 'Nie udało się dodać zadania. Spróbuj ponownie.';
                    await this.fetchNormalizer(true);
                }
            } finally {
                this.submittingNormalizer = '';
                this.normalizerActionController = null;
            }
        },

        async fetchBeetsStatus() {
            if (this.fetchingBeets) return;
            this.fetchingBeets = true;
            try {
                const response = await fetch('/api/beets/status');
                if (!response.ok) throw new Error('Status unavailable');
                const data = await response.json();
                if (!data || typeof data.enabled !== 'boolean' || typeof data.available !== 'boolean') {
                    throw new Error('Invalid status');
                }
                this.beetsStatus = data;
                this.beetsStatusError = '';
                if (data.enabled) {
                    await this.fetchBeetsReview();
                } else {
                    this.beetsReviewItems = [];
                    this.beetsReviewError = '';
                }
            } catch (error) {
                this.beetsStatus = null;
                this.beetsStatusError = 'Nie udało się sprawdzić stanu. Odśwież, aby spróbować ponownie.';
                this.beetsReviewItems = [];
                this.beetsReviewError = 'Lista będzie dostępna po przywróceniu połączenia z serwerem.';
            } finally {
                this.fetchingBeets = false;
            }
        },

        async fetchBeetsReview() {
            if (this.fetchingBeetsReview) return;
            this.fetchingBeetsReview = true;
            try {
                const response = await fetch('/api/beets/review');
                if (!response.ok) throw new Error('Review unavailable');
                const data = await response.json();
                if (!Array.isArray(data.items)) throw new Error('Invalid review');
                this.beetsReviewItems = data.items.filter(item => item && typeof item.id === 'string'
                    && !(item.imported && item.metadata_confirmed && item.release_confirmed
                        && !item.artwork_pending && !item.source_changed));
                this.beetsReviewError = '';
            } catch (error) {
                this.beetsReviewItems = [];
                this.beetsReviewError = 'Nie udało się wczytać listy. Odśwież stan, aby spróbować ponownie.';
            } finally {
                this.fetchingBeetsReview = false;
            }
        },

        async importBeets(retry = false, reviewId = null) {
            if (!this.canImportBeets) return;
            if (reviewId !== null && !this.beetsReviewItems.some(item => item.id === reviewId)) return;
            this.importingBeets = true;
            this.retryingBeetsReviewId = reviewId;
            this.beetsImportError = '';
            this.statusMsg = '';
            this.statusError = false;
            try {
                const url = reviewId === null ? '/api/beets/import'
                    : `/api/beets/review/${encodeURIComponent(reviewId)}/retry`;
                const payload = { navi_sync: this.naviSync, navi_account_id: this.selectedNaviAccount };
                if (reviewId === null) payload.retry = retry;
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || !data.task_id) {
                    throw new Error(typeof data.detail === 'string' ? data.detail : 'Nie udało się rozpocząć importu.');
                }
                this.statusMsg = data.message || 'Rozpoczęto import nowych plików.';
                this.connectLogs(data.task_id);
                this.fetchJobs();
                this.fetchBeetsStatus();
            } catch (error) {
                this.statusError = true;
                this.statusMsg = error instanceof TypeError ? 'Nie udało się połączyć z serwerem. Spróbuj ponownie.'
                    : (error.message || 'Błąd komunikacji z serwerem.');
                this.beetsImportError = this.statusMsg;
                this.fetchBeetsStatus();
            } finally {
                this.importingBeets = false;
                this.retryingBeetsReviewId = null;
            }
        },

        shouldShowStatusBanner() {
            if (this.statusDisplayMode === 'always') return true;
            const naviNotReady = !this.selectedNaviAccount || !this.naviStatus.ok;
            const slskdNotReady = !this.slskdAccount.configured || !this.slskdStatus.ok;
            return Boolean(naviNotReady || slskdNotReady);
        },

        async testNavidrome() {
            if (!this.selectedNaviAccount) {
                this.naviStatus = { tested: false, ok: false, msg: 'Brak wybranego konta Navidrome' };
                return;
            }
            this.testingNavi = true;
            try {
                const res = await fetch('/api/test/navidrome', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        account_id: this.selectedNaviAccount
                    })
                });
                const data = await res.json();
                this.naviStatus = {
                    tested: true,
                    ok: Boolean(data.ok),
                    msg: data.message || (data.ok ? 'Połączono pomyślnie z Navidrome!' : 'Błąd połączenia')
                };
            } catch (e) {
                this.naviStatus = {
                    tested: true,
                    ok: false,
                    msg: 'Nie udało się połączyć z serwerem'
                };
            } finally {
                this.testingNavi = false;
            }
        },

        async testSlskd() {
            if (!this.slskdAccount || !this.slskdAccount.configured) {
                this.slskdStatus = { tested: false, ok: false, msg: 'Brak konfiguracji w slskd_accounts.txt' };
                return;
            }
            this.testingSlskd = true;
            try {
                const res = await fetch('/api/test/slskd', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await res.json();
                this.slskdStatus = {
                    tested: true,
                    ok: Boolean(data.ok),
                    msg: data.message || (data.ok ? 'Połączono i zalogowano pomyślnie do Soulseek (slskd)!' : 'Błąd połączenia')
                };
            } catch (e) {
                this.slskdStatus = {
                    tested: true,
                    ok: false,
                    msg: 'Nie udało się połączyć z serwerem'
                };
            } finally {
                this.testingSlskd = false;
            }
        },

        async checkServicesStatus() {
            const promises = [];
            if (this.selectedNaviAccount) {
                promises.push(this.testNavidrome());
            } else {
                this.naviStatus = { tested: false, ok: false, msg: '' };
            }
            if (this.slskdAccount && this.slskdAccount.configured) {
                promises.push(this.testSlskd());
            } else {
                this.slskdStatus = { tested: false, ok: false, msg: '' };
            }
            if (promises.length > 0) {
                await Promise.allSettled(promises);
            }
        },

        async organizeSlskd() {
            if (this.importingBeets || this.organizingSlskd) return;
            if (!this.slskdAccount || !this.slskdAccount.configured) {
                this.statusMsg = 'Brak konfiguracji w slskd_accounts.txt.';
                this.statusError = true;
                return;
            }
            this.organizingSlskd = true;
            this.statusMsg = '';
            this.statusError = false;
            this.logs = ['Rozpoczynanie porządkowania pobranych plików Soulseek...'];

            try {
                const response = await fetch('/api/slskd/organize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        slskd_url: this.slskdAccount.server || '',
                        navi_sync: this.naviSync,
                        navi_account_id: this.selectedNaviAccount
                    })
                });

                const data = await response.json();

                if (response.ok && data.task_id) {
                    this.statusMsg = data.message;
                    this.connectLogs(data.task_id);
                    this.fetchJobs();
                } else {
                    this.statusError = true;
                    this.statusMsg = data.detail || 'Wystąpił błąd przy zlecaniu porządkowania.';
                }
            } catch (error) {
                this.statusError = true;
                this.statusMsg = 'Błąd komunikacji z serwerem.';
            } finally {
                this.organizingSlskd = false;
            }
        },
        
        async startDownload() {
            if (!this.url.trim() || this.loading || this.importingBeets) return;
            
            this.loading = true;
            this.statusMsg = '';
            this.statusError = false;
            
            // clear old logs
            this.logs = ['Zlecanie zadania do serwera...'];
            
            try {
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tool: this.selectedTool,
                        url: this.url,
                        flags: this.flags,
                        navi_account_id: this.selectedNaviAccount,
                        navi_sync: this.naviSync
                    })
                });
                
                const data = await response.json();
                
                if (response.ok && data.task_id) {
                    this.statusMsg = data.message;
                    this.url = ''; // clear input
                    
                    // Connect to SSE logs for this task
                    this.connectLogs(data.task_id);
                    this.fetchJobs();
                    
                } else {
                    this.statusError = true;
                    this.statusMsg = data.detail || 'Wystąpił błąd przy zlecaniu pobierania.';
                    this.fetchErrors();
                }
            } catch (error) {
                this.statusError = true;
                this.statusMsg = 'Błąd komunikacji z serwerem.';
                this.fetchErrors();
            } finally {
                this.loading = false;
            }
        },

        async fetchSpotifyMetadata() {
            if (!this.url || this.fetchingMeta) return;
            this.fetchingMeta = true;
            this.statusMsg = '';
            this.statusError = false;
            try {
                const res = await fetch('/api/spotify/metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: this.url })
                });
                const data = await res.json();
                if (res.ok && data.ok) {
                    this.spotifyMetadata = data;
                } else {
                    this.statusError = true;
                    this.statusMsg = data.detail || data.message || 'Nie udało się pobrać metadanych Spotify.';
                }
            } catch (err) {
                console.error('Błąd pobierania metadanych Spotify:', err);
                this.statusError = true;
                this.statusMsg = 'Błąd połączenia z serwerem przy pobieraniu metadanych.';
            } finally {
                this.fetchingMeta = false;
            }
        },

        copyFallback(text) {
            try {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                textarea.style.top = '-9999px';
                textarea.setAttribute('readonly', '');
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            } catch (err) {
                console.warn('Fallback clipboard copy failed', err);
            }
        },

        copyMetadataCommand() {
            if (!this.spotifyMetadata || !this.spotifyMetadata.command) return;
            const cmd = this.spotifyMetadata.command;

            // Copy to clipboard with fallback for non-secure HTTP contexts (e.g. LAN IP)
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(cmd).catch(() => {
                    this.copyFallback(cmd);
                });
            } else {
                this.copyFallback(cmd);
            }

            // Append command to logs
            this.logs.push('[Metadane FFmpeg] Wygenerowano komendę:\n' + cmd);

            // Auto-scroll the live terminal to bottom
            this.$nextTick(() => {
                const container = document.getElementById('log-container');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            });

            // Show feedback for 2.5 seconds
            this.copiedMetaCommand = true;
            if (this.copiedMetaFeedbackTimer) {
                clearTimeout(this.copiedMetaFeedbackTimer);
            }
            this.copiedMetaFeedbackTimer = setTimeout(() => {
                this.copiedMetaCommand = false;
                this.copiedMetaFeedbackTimer = null;
            }, 2500);
        }
    }
}
