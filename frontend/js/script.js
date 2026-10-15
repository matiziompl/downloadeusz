function appData() {
    return {
        // App State
        settingsOpen: false,
        loading: false,
        statusMsg: '',
        statusError: false,
        
        // SSE Event Source
        eventSource: null,
        logs: [],
        
        // Services & Integrations
        // Services & Integrations (persisted in localStorage)
        slskdUrl: localStorage.getItem('slskdUrl') || '',
        slskdUser: localStorage.getItem('slskdUser') || '',
        slskdPass: localStorage.getItem('slskdPass') || '',
        naviUrl: localStorage.getItem('naviUrl') || '',
        naviUser: localStorage.getItem('naviUser') || '',
        naviPass: localStorage.getItem('naviPass') || '',
        naviSync: localStorage.getItem('naviSync') === 'true',
        statusDisplayMode: (localStorage.getItem('statusDisplayMode') && localStorage.getItem('statusDisplayMode') !== 'errors_only') ? localStorage.getItem('statusDisplayMode') : 'not_logged_or_error',
        naviStatus: { tested: false, ok: false, msg: '' },
        slskdStatus: { tested: false, ok: false, msg: '' },
        testingNavi: false,
        testingSlskd: false,
        organizingSlskd: false,

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
            this.fetchWallpapers();
            this.fetchErrors();
            this.checkServicesStatus();
            
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
                if (!value) return;
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
            
            // Watchers for navidrome credentials
            this.$watch('naviUrl', value => {
                localStorage.setItem('naviUrl', value);
                this.naviStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('naviUser', value => {
                localStorage.setItem('naviUser', value);
                this.naviStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('naviPass', value => {
                localStorage.setItem('naviPass', value || '');
                this.naviStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('naviSync', value => {
                localStorage.setItem('naviSync', value ? 'true' : 'false');
                this.saveSettings();
            });
            
            // Watchers for soulseek credentials
            this.$watch('slskdUrl', value => {
                localStorage.setItem('slskdUrl', value);
                this.slskdStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('slskdUser', value => {
                localStorage.setItem('slskdUser', value);
                this.slskdStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });
            this.$watch('slskdPass', value => {
                localStorage.setItem('slskdPass', value || '');
                this.slskdStatus = { tested: false, ok: false, msg: '' };
                this.saveSettings();
            });

            this.$nextTick(() => {
                this.updateExtractedColor();
            });
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

            // Persist service addresses, logins, passwords, and sync settings
            localStorage.setItem('slskdUrl', this.slskdUrl || '');
            localStorage.setItem('slskdUser', this.slskdUser || '');
            localStorage.setItem('slskdPass', this.slskdPass || '');
            localStorage.setItem('naviUrl', this.naviUrl || '');
            localStorage.setItem('naviUser', this.naviUser || '');
            localStorage.setItem('naviPass', this.naviPass || '');
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
                            navidrome_skip_mode: (settings.flags.navidrome_skip_mode === 'all') ? 'all' : 'flac_only'
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
            this.slskdUrl = localStorage.getItem('slskdUrl') || this.slskdUrl || '';
            this.slskdUser = localStorage.getItem('slskdUser') || this.slskdUser || '';
            this.slskdPass = localStorage.getItem('slskdPass') || this.slskdPass || '';
            this.naviUrl = localStorage.getItem('naviUrl') || this.naviUrl || '';
            this.naviUser = localStorage.getItem('naviUser') || this.naviUser || '';
            this.naviPass = localStorage.getItem('naviPass') || this.naviPass || '';
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
        
        connectLogs(taskId) {
            if (this.eventSource) {
                this.eventSource.close();
            }
            
            this.logs = [];
            this.eventSource = new EventSource(`/api/logs/${taskId}`);
            
            this.eventSource.onmessage = (event) => {
                this.logs.push(event.data);
                
                // Auto scroll to bottom
                this.$nextTick(() => {
                    const container = document.getElementById('log-container');
                    if (container) {
                        container.scrollTop = container.scrollHeight;
                    }
                });
                
                if (event.data.includes('[Zakończono') || event.data.includes('[Błąd]')) {
                    this.eventSource.close();
                    this.loading = false;
                    this.organizingSlskd = false;
                    this.fetchErrors(); // Fetch errors after download finishes
                }
            };
            
            this.eventSource.onerror = () => {
                this.eventSource.close();
                this.loading = false;
                this.organizingSlskd = false;
                this.logs.push("Utracono połączenie z logami.");
                this.fetchErrors();
            };
        },
        
        shouldShowStatusBanner() {
            if (this.statusDisplayMode === 'always') return true;
            // shortcut: enforces both Navidrome and Soulseek to be logged in before hiding banner in 'not_logged_or_error' mode, per user specification
            const naviNotReady = !this.naviUrl || !this.naviUser || !this.naviPass || !this.naviStatus.ok;
            const slskdNotReady = !this.slskdUrl || !this.slskdUser || !this.slskdPass || !this.slskdStatus.ok;
            return Boolean(naviNotReady || slskdNotReady);
        },

        async testNavidrome() {
            if (!this.naviUrl) {
                this.naviStatus = { tested: false, ok: false, msg: 'Brak adresu URL Navidrome' };
                return;
            }
            this.testingNavi = true;
            try {
                const res = await fetch('/api/test/navidrome', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.naviUrl,
                        user: this.naviUser,
                        password: this.naviPass
                    })
                });
                const data = await res.json();
                this.naviStatus = {
                    tested: true,
                    ok: !!data.ok,
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
            if (!this.slskdUrl) {
                this.slskdStatus = { tested: false, ok: false, msg: 'Brak adresu URL Soulseek (slskd)' };
                return;
            }
            this.testingSlskd = true;
            try {
                const res = await fetch('/api/test/slskd', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.slskdUrl,
                        username: this.slskdUser,
                        password: this.slskdPass
                    })
                });
                const data = await res.json();
                this.slskdStatus = {
                    tested: true,
                    ok: !!data.ok,
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
            if (this.naviUrl) {
                promises.push(this.testNavidrome());
            } else {
                this.naviStatus = { tested: false, ok: false, msg: '' };
            }
            if (this.slskdUrl) {
                promises.push(this.testSlskd());
            } else {
                this.slskdStatus = { tested: false, ok: false, msg: '' };
            }
            if (promises.length > 0) {
                await Promise.allSettled(promises);
            }
        },

        async organizeSlskd() {
            if (!this.slskdUrl) {
                this.statusMsg = 'Brak adresu URL Soulseek (slskd). Skonfiguruj go w ustawieniach.';
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
                        slskd_url: this.slskdUrl,
                        slskd_user: this.slskdUser,
                        slskd_pass: this.slskdPass,
                        navi_sync: this.naviSync,
                        navi_url: this.naviUrl,
                        navi_user: this.naviUser,
                        navi_pass: this.naviPass
                    })
                });

                const data = await response.json();

                if (response.ok && data.task_id) {
                    this.statusMsg = data.message;
                    this.connectLogs(data.task_id);
                } else {
                    this.statusError = true;
                    this.statusMsg = data.detail || 'Wystąpił błąd przy zlecaniu porządkowania.';
                    this.organizingSlskd = false;
                }
            } catch (error) {
                this.statusError = true;
                this.statusMsg = 'Błąd komunikacji z serwerem.';
                this.organizingSlskd = false;
            }
        },
        
        async startDownload() {
            if (!this.url) return;
            
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
                        navi_url: this.naviUrl,
                        navi_user: this.naviUser,
                        navi_pass: this.naviPass,
                        navi_sync: this.naviSync
                    })
                });
                
                const data = await response.json();
                
                if (response.ok && data.task_id) {
                    this.statusMsg = data.message;
                    this.url = ''; // clear input
                    
                    // Connect to SSE logs for this task
                    this.connectLogs(data.task_id);
                    
                } else {
                    this.statusError = true;
                    this.statusMsg = data.detail || 'Wystąpił błąd przy zlecaniu pobierania.';
                    this.loading = false;
                    this.fetchErrors();
                }
            } catch (error) {
                this.statusError = true;
                this.statusMsg = 'Błąd komunikacji z serwerem.';
                this.loading = false;
                this.fetchErrors();
            }
        }
    }
}
