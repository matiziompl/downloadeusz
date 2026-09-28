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
            m3u_name: '' // empty means {list}
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
        
        wallpapers: [],
        selectedImage: '',
        
        errorsList: [],
        errorsOpen: false,
        
        logMode: 'advanced', // changed default to advanced since user wants terminal
        
        naviUrl: localStorage.getItem('naviUrl') || '',
        naviUser: localStorage.getItem('naviUser') || '',
        naviPass: localStorage.getItem('naviPass') || '',
        naviSync: localStorage.getItem('naviSync') === 'true',

        initApp() {
            this.loadSettings();
            this.fetchWallpapers();
            this.fetchErrors();
            
            // Watch for changes and save to local storage
            this.$watch('showTitle', () => this.saveSettings());
            this.$watch('themeColor', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('bgType', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('bgPreset', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('selectedImage', () => { this.saveSettings(); this.updateExtractedColor(); });
            this.$watch('blurAmount', () => this.saveSettings());
            this.$watch('whiteGradient', () => this.saveSettings());
            this.$watch('logMode', () => this.saveSettings());
            this.$watch('flags', () => this.saveSettings(), { deep: true });
            
            // Watchers for navidrome credentials
            this.$watch('naviUrl', value => localStorage.setItem('naviUrl', value));
            this.$watch('naviUser', value => localStorage.setItem('naviUser', value));
            this.$watch('naviPass', value => localStorage.setItem('naviPass', value));
            this.$watch('naviSync', value => localStorage.setItem('naviSync', value));
            
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
                flags: this.flags
            };
            localStorage.setItem('downloadeusz_settings', JSON.stringify(settings));
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
                    
                    if (settings.flags) {
                        this.flags = { ...this.flags, ...settings.flags };
                    }
                } catch (e) {}
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
        
        getEffectiveColor() {
            if (this.themeColor === 'auto') {
                return this.extractedColor || '#ffffff';
            }
            return this.themeColor;
        },
        
        rgbToHex(r, g, b) {
            return "#" + (1 << 24 | r << 16 | g << 8 | b).toString(16).slice(1);
        },
        
        updateExtractedColor() {
            if (this.themeColor !== 'auto') return;
            
            if (this.bgType === 'preset') {
                this.extractedColor = this.presetColors[this.bgPreset] || '#3b82f6';
            } else if (this.bgType === 'image' && this.selectedImage) {
                // Use ColorThief to extract dominant color
                const img = new Image();
                img.crossOrigin = 'Anonymous';
                img.onload = () => {
                    try {
                        const colorThief = new ColorThief();
                        const color = colorThief.getColor(img);
                        this.extractedColor = this.rgbToHex(color[0], color[1], color[2]);
                    } catch (e) {
                        console.error("ColorThief failed", e);
                        this.extractedColor = '#ffffff';
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
                    this.fetchErrors(); // Fetch errors after download finishes
                }
            };
            
            this.eventSource.onerror = () => {
                this.eventSource.close();
                this.loading = false;
                this.logs.push("Utracono połączenie z logami.");
                this.fetchErrors();
            };
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
