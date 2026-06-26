/* --- Anki Lock-In JavaScript Core --- */

(function () {
  // If already initialized, recreate UI for the new page and reset the review session
  if (window.AnkiLockIn) {
    if (window.AnkiLockInConfig) {
      window.AnkiLockIn.updateConfig(window.AnkiLockInConfig);
    } else {
      window.AnkiLockIn.createUI();
    }
    window.AnkiLockIn.resetSession();
    return;
  }

  // Load configuration injected by Python backend, or use defaults
  let config = window.AnkiLockInConfig || {
    health_max: 100,
    health_drain_rate: 2.0,
    health_heal_easy: 15,
    health_heal_good: 10,
    health_heal_hard: 5,
    health_heal_again: -10,
    pomodoro_study_duration: 25,
    pomodoro_break_duration: 5,
    pomodoro_enable_sound: true,
    speed_focus_alert_delay: 8,
    speed_focus_reveal_delay: 0,
    speed_focus_fail_delay: 0,
    speed_focus_volume: 0.5,
    no_distraction_enabled: true,
    no_distraction_auto_fullscreen: true,
    adhd_boosters_streak_enabled: true,
    adhd_boosters_shield_enabled: true,
    adhd_boosters_autopause_enabled: true,
    adhd_boosters_low_health_heartbeat: true,
    preset_mode: "balanced",
    grace_period_duration: 180,
    sigmoid_drain_enabled: true,
    streak_reset_idle_time: 30,
    pomodoro_visible: true,
    bar_position: "bottom",
    custom_answer_buttons: true,
    hide_answer_buttons: false
  };

  // State for custom answer buttons overlay is now pushed from Python via
  // window.AnkiLockIn.updateButtons(buttonsData) which rebuilds the row.
  // Intervals live only in the DOM; no client-side cache is needed.

  // Safe python command bridge caller
  function safePycmd(message) {
    if (typeof pycmd !== 'undefined') {
      pycmd(message);
    } else if (typeof window.pycmd !== 'undefined') {
      window.pycmd(message);
    } else {
      setTimeout(() => {
        if (typeof pycmd !== 'undefined') {
          pycmd(message);
        } else if (typeof window.pycmd !== 'undefined') {
          window.pycmd(message);
        }
      }, 50);
    }
  }

  // Determine webview kind
  let isBottomWebview = false;
  let isMainWebview = true;
  let isDeckBrowserWebview = false;
  let isOverviewWebview = false;

  function determineWebviewKind() {
    isBottomWebview = false;
    isMainWebview = false;
    isDeckBrowserWebview = false;
    isOverviewWebview = false;

    // Check for elements that are unique to the bottom webview
    const hasBottomElements = !!(
      document.getElementById('bottom') ||
      document.getElementById('outer') || 
      document.getElementById('bottombar') || 
      document.querySelector('.reviewer-bottom-bar') ||
      document.querySelector('.bottom-bar') ||
      document.getElementById('ansbuts') || 
      document.getElementById('buttons') || 
      document.getElementById('wrapper') ||
      document.querySelector('button[onclick*="ease"]') ||
      document.querySelector('input[onclick*="ease"]')
    );

    if (window.AnkiLockInWebviewKind) {
      isBottomWebview = (window.AnkiLockInWebviewKind === 'bottom') || hasBottomElements;
      isMainWebview = (window.AnkiLockInWebviewKind === 'main') && !hasBottomElements;
      isDeckBrowserWebview = (window.AnkiLockInWebviewKind === 'deckBrowser');
      isOverviewWebview = (window.AnkiLockInWebviewKind === 'overview');
    } else {
      isBottomWebview = hasBottomElements ||
                        window.location.href.includes('bottom') || 
                        document.body.classList.contains('bottom') ||
                        document.body.id === 'bottom';
      
      isDeckBrowserWebview = window.location.href.includes('deckbrowser') || 
                             document.body.classList.contains('deckbrowser') ||
                             !!document.getElementById('deck') ||
                             !!document.querySelector('.deck-list');

      isOverviewWebview = window.location.href.includes('overview') ||
                          document.body.classList.contains('overview') ||
                          !!document.querySelector('.overview') ||
                          !!document.getElementById('overview');
      
      if (!isBottomWebview && !isDeckBrowserWebview && !isOverviewWebview) {
        isMainWebview = true;
      }
    }
  }

  // State Variables
  let health = config.health_max;
  let streak = 0;
  let shieldTimeLeft = 0;
  let pomoState = 'study'; // 'study' | 'break' | 'paused'
  let pomoTimeLeft = config.pomodoro_study_duration * 60;
  if (window.AnkiLockInPomoState && window.AnkiLockInPomoState.time_left !== -1) {
    pomoState = window.AnkiLockInPomoState.state;
    pomoTimeLeft = window.AnkiLockInPomoState.time_left;
  }
  
  let cardStartTime = Date.now();
  let cardAnswered = false;
  let alertSoundPlayed = false;
  
  let lastTickTime = Date.now();
  let lastHeartbeatTime = 0;
  let lastPomoSyncTime = 0;
  let breakStartTime = 0;
  let isWindowFocused = true;
  let gameOverFreeze = false;
  let gameOverTimeout = null;
  let lastAction = null; // Stores last answer state to revert on undo

  // Session Statistics
  let statsCards = 0;
  let statsCorrect = 0;
  let statsMaxStreak = 0;
  let statsShields = 0;
  let statsStudyTime = 0;
  let activeStudyDuration = 0; // accumulated focus study time in seconds

  // Web Audio Context
  let audioCtx = null;

  function initAudio() {
    if (audioCtx) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
      console.error("AudioContext initialization failed", e);
    }
  }

  function getAudioContext() {
    initAudio();
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    return audioCtx;
  }

  // --- Sound Synthesizers (Web Audio API) ---
  // Polished design: layered oscillators, filtering, proper envelopes, stereo depth.

  // Helper: Create a filtered tone with quick-attack / decay envelope.
  // Returns the gain node so callers can add extra shaping.
  function _voice(ctx, freq, opts) {
    opts = opts || {};
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const filter = ctx.createBiquadFilter();

    osc.type = opts.type || 'sine';
    osc.frequency.value = freq;
    if (opts.detune) osc.detune.value = opts.detune;

    filter.type = opts.filterType || 'lowpass';
    filter.frequency.value = opts.filterFreq || 3000;
    filter.Q.value = opts.filterQ !== undefined ? opts.filterQ : 0.7;

    osc.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);

    const now = ctx.currentTime + (opts.delay || 0);
    const peak = opts.peak !== undefined ? opts.peak : 0.3;
    const dur = opts.duration || 0.3;

    gain.gain.setValueAtTime(0.001, now);
    gain.gain.linearRampToValueAtTime(peak, now + 0.004); // very fast attack
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

    osc.start(now);
    osc.stop(now + dur + 0.05);

    return { osc, gain, filter };
  }

  function playSound(type) {
    const ctx = getAudioContext();
    if (!ctx) return;

    const vol = config.speed_focus_volume;
    const now = ctx.currentTime;

    if (type === 'alert') {
      // Soft urgent ping — dual detuned triangles with bell resonance.
      // Two tones 10 Hz apart create a gentle wobble. Lowpass with moderate Q
      // adds a bell-like ring. Much softer than the old sawtooth buzzer.
      _voice(ctx, 880, { type: 'triangle', peak: vol * 0.25, duration: 0.45, filterFreq: 1200, filterQ: 4, detune: -5 });
      _voice(ctx, 890, { type: 'triangle', peak: vol * 0.25, duration: 0.45, filterFreq: 1200, filterQ: 4, detune: 5, delay: 0.02 });
    }
    else if (type === 'heartbeat') {
      // Deep resonant thud — pure sine sub-bass with heavy lowpass.
      // Two quick thumps 0.22s apart, filtered to sound like a muffled
      // heartbeat through a stethoscope.
      const thump = function(t) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        const filter = ctx.createBiquadFilter();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(48, t);
        osc.frequency.exponentialRampToValueAtTime(20, t + 0.18);
        filter.type = 'lowpass';
        filter.frequency.value = 70;
        filter.Q.value = 1.5;
        gain.gain.setValueAtTime(vol * 0.85, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
        osc.connect(filter);
        filter.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t);
        osc.stop(t + 0.2);
      };
      thump(now);
      thump(now + 0.22);
    }
    else if (type === 'success') {
      // Sparkly ascending triad — C5→E5→G5 with triangle waves.
      // Each note has a slight bell-like resonance via lowpass Q=3.
      // A soft high overtone (sine 2x freq) adds sparkle.
      var notes = [
        { freq: 523.25, delay: 0 },     // C5
        { freq: 659.25, delay: 0.07 },  // E5
        { freq: 783.99, delay: 0.14 }   // G5
      ];
      notes.forEach(function(n) {
        _voice(ctx, n.freq, { type: 'triangle', peak: vol * 0.22, duration: 0.35, filterFreq: 2000, filterQ: 3, delay: n.delay });
        // Sparkle overtone
        _voice(ctx, n.freq * 2, { type: 'sine', peak: vol * 0.07, duration: 0.25, delay: n.delay + 0.02 });
      });
    }
    else if (type === 'shield_up') {
      // Rising protective shimmer — layered bass drone + rising sweep.
      // The bass triangle anchors the sound, while a sine sweeps C4→C6
      // through a lowpass filter that opens up, creating a "shimmer" effect.
      // Bass drone (C4, held)
      _voice(ctx, 261.63, { type: 'triangle', peak: vol * 0.2, duration: 0.7, filterFreq: 600, filterQ: 1 });
      // Rising shimmer sweep
      var swOsc = ctx.createOscillator();
      var swGain = ctx.createGain();
      var swFilter = ctx.createBiquadFilter();
      swOsc.type = 'sine';
      swOsc.frequency.setValueAtTime(261.63, now);
      swOsc.frequency.exponentialRampToValueAtTime(1046.50, now + 0.55);
      swFilter.type = 'lowpass';
      swFilter.frequency.setValueAtTime(300, now);
      swFilter.frequency.exponentialRampToValueAtTime(4000, now + 0.55);
      swFilter.Q.value = 1.5;
      swGain.gain.setValueAtTime(0.001, now);
      swGain.gain.linearRampToValueAtTime(vol * 0.25, now + 0.05);
      swGain.gain.exponentialRampToValueAtTime(0.001, now + 0.65);
      swOsc.connect(swFilter);
      swFilter.connect(swGain);
      swGain.connect(ctx.destination);
      swOsc.start(now);
      swOsc.stop(now + 0.7);
    }
    else if (type === 'break_start') {
      // Peaceful chime cascade — C major arpeggio with soft attack and long decay.
      // Very gentle: pure sine waves, slow attack for a "meditation bowl" feel.
      // Slight detuning between note pairs for warmth.
      if (!config.pomodoro_enable_sound) return;
      var chord = [
        { freq: 261.63, delay: 0 },      // C4
        { freq: 329.63, delay: 0.15 },   // E4
        { freq: 392.00, delay: 0.30 },   // G4
        { freq: 523.25, delay: 0.45 }    // C5
      ];
      chord.forEach(function(n) {
        var t = now + n.delay;
        var g = ctx.createGain();
        var o = ctx.createOscillator();
        o.type = 'sine';
        o.frequency.value = n.freq;
        o.detune.value = 3; // slight sharpness for warmth
        g.gain.setValueAtTime(0.001, t);
        g.gain.linearRampToValueAtTime(vol * 0.15, t + 0.08);  // slow attack
        g.gain.exponentialRampToValueAtTime(0.001, t + 1.5);    // long decay
        o.connect(g);
        g.connect(ctx.destination);
        o.start(t);
        o.stop(t + 1.6);
        // Slightly detuned duplicate for chorusing
        var o2 = ctx.createOscillator();
        var g2 = ctx.createGain();
        o2.type = 'sine';
        o2.frequency.value = n.freq;
        o2.detune.value = -4;
        g2.gain.setValueAtTime(0.001, t);
        g2.gain.linearRampToValueAtTime(vol * 0.08, t + 0.08);
        g2.gain.exponentialRampToValueAtTime(0.001, t + 1.3);
        o2.connect(g2);
        g2.connect(ctx.destination);
        o2.start(t);
        o2.stop(t + 1.4);
      });
    }
    else if (type === 'break_end') {
      // Gentle wake-up call — ascending E4→G4→C5 with warm filtered triangles.
      // Brighter than break_start but still soft. Quick tempo.
      if (!config.pomodoro_enable_sound) return;
      var notes = [
        { freq: 329.63, delay: 0 },      // E4
        { freq: 392.00, delay: 0.1 },    // G4
        { freq: 523.25, delay: 0.2 }     // C5
      ];
      notes.forEach(function(n) {
        _voice(ctx, n.freq, { type: 'triangle', peak: vol * 0.25, duration: 0.5, filterFreq: 2500, filterQ: 1.5, delay: n.delay });
      });
    }
    else if (type === 'gameover') {
      // Deep dramatic descent — layered sub-bass + filtered buzz.
      // Sine at 55Hz provides weight, triangle at 110→55Hz provides
      // the dramatic pitch drop, heavily lowpassed to avoid harshness.
      // Sounds like a distant ship horn, not a piercing alarm.

      // Sub-bass foundation (clean sine, 55Hz)
      var subOsc = ctx.createOscillator();
      var subGain = ctx.createGain();
      subOsc.type = 'sine';
      subOsc.frequency.value = 55;
      subGain.gain.setValueAtTime(vol * 0.7, now);
      subGain.gain.exponentialRampToValueAtTime(0.001, now + 0.9);
      subOsc.connect(subGain);
      subGain.connect(ctx.destination);
      subOsc.start(now);
      subOsc.stop(now + 1.0);

      // Dramatic pitch drop (triangle, 110→55Hz, heavily filtered)
      var dropOsc = ctx.createOscillator();
      var dropGain = ctx.createGain();
      var dropFilter = ctx.createBiquadFilter();
      dropOsc.type = 'triangle';
      dropOsc.frequency.setValueAtTime(110, now + 0.05);
      dropOsc.frequency.exponentialRampToValueAtTime(55, now + 0.65);
      dropFilter.type = 'lowpass';
      dropFilter.frequency.setValueAtTime(180, now);
      dropFilter.frequency.exponentialRampToValueAtTime(80, now + 0.7);
      dropFilter.Q.value = 0.5;
      dropGain.gain.setValueAtTime(0.001, now);
      dropGain.gain.linearRampToValueAtTime(vol * 0.6, now + 0.08);
      dropGain.gain.setValueAtTime(vol * 0.4, now + 0.5);
      dropGain.gain.exponentialRampToValueAtTime(0.001, now + 0.85);
      dropOsc.connect(dropFilter);
      dropFilter.connect(dropGain);
      dropGain.connect(ctx.destination);
      dropOsc.start(now);
      dropOsc.stop(now + 1.0);

      // Slight overtone for richness (sine 110Hz, very quiet)
      _voice(ctx, 110, { type: 'sine', peak: vol * 0.12, duration: 0.7, filterFreq: 200, delay: 0.05 });
    }
  }

  // --- Python Communication & Pomodoro Sync ---
  
  function syncPomoState() {
    safePycmd("lockin-pomo-sync:" + JSON.stringify({ state: pomoState, time_left: pomoTimeLeft }));
  }

  function togglePomodoro() {
    initAudio();
    if (pomoState === 'study' || pomoState === 'break') {
      pomoState = 'paused';
    } else if (pomoState === 'paused') {
      pomoState = (window.AnkiLockInPomoState && window.AnkiLockInPomoState.state === 'break') ? 'break' : 'study';
    }
    syncPomoState();
    updateUI();
  }

  function stopPomodoro() {
    initAudio();
    pomoState = 'paused';
    pomoTimeLeft = config.pomodoro_study_duration * 60;
    
    // Hide break overlay if visible
    const overlay = document.getElementById('lockin-break-overlay');
    if (overlay) overlay.classList.remove('overlay-open');
    
    syncPomoState();
    updateUI();
  }

  function sendStatsToPython() {
    let stats = {
      cards: statsCards,
      correct: statsCorrect,
      max_streak: statsMaxStreak,
      shields: statsShields,
      study_time: activeStudyDuration
    };
    safePycmd("anki-lockin-stats:" + JSON.stringify(stats));
  }

  // --- Theme Awareness ---
  
  function detectTheme() {
    const isDark = document.body.classList.contains('nightMode') || 
                   document.body.classList.contains('night_mode') || 
                   document.body.classList.contains('night-mode') || 
                   (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (isDark) {
      document.body.classList.add('lockin-dark-theme');
      document.body.classList.remove('lockin-light-theme');
    } else {
      document.body.classList.add('lockin-light-theme');
      document.body.classList.remove('lockin-dark-theme');
    }
  }

  // --- UI Creation ---
 
  function createUI() {
    try {
      safePycmd("lockin-debug:" + JSON.stringify({
        url: window.location.href,
        title: document.title,
        body_classes: document.body ? document.body.className : "",
        body_id: document.body ? document.body.id : "",
        html_snippet: document.body ? document.body.innerHTML.substring(0, 500) : "",
        readyState: document.readyState
      }));
    } catch (e) {
      safePycmd("lockin-debug:" + JSON.stringify({ error: e.message }));
    }

    determineWebviewKind();

    // Add active class unconditionally to document.body
    document.body.classList.add('lockin-active');

    // Add specific body classes depending on webview kind
    if (isMainWebview) {
      document.body.classList.add('lockin-main');
    } else if (isBottomWebview) {
      document.body.classList.add('lockin-bottom-webview');
    } else if (isDeckBrowserWebview) {
      document.body.classList.add('lockin-deckbrowser');
    } else if (isOverviewWebview) {
      document.body.classList.add('lockin-overview');
    }

    if (config.hide_answer_buttons) {
      document.body.classList.add('lockin-hide-buttons');
    } else {
      document.body.classList.remove('lockin-hide-buttons');
    }

    const showBarHere = isMainWebview;

    if (showBarHere) {
      if (!document.getElementById('lockin-bar')) {
        const bar = document.createElement('div');
        bar.id = 'lockin-bar';
        bar.innerHTML = `
          <div class="lockin-left-section">
            <div id="lockin-health-badge">HP 100</div>
            <div class="lockin-health-bar-container">
              <div id="lockin-health-fill" style="width: 100%"></div>
              <div id="lockin-shield-overlay"></div>
            </div>
          </div>
          <div class="lockin-center-section">
            <div id="lockin-streak-badge">⚡ STREAK x0</div>
          </div>
          <div class="lockin-right-section">
            <div id="lockin-pomo-status">
              <span class="lockin-dot"></span>
              <span id="lockin-pomo-timer">25:00</span>
            </div>
            <button class="lockin-btn" id="lockin-exit-btn" title="Exit Review & Return to Decks">✕ Exit</button>
          </div>
        `;
        document.body.appendChild(bar);
        
        // Bind click handler for exit button
        document.getElementById('lockin-exit-btn').addEventListener('click', () => {
          safePycmd("anki-lockin-exit");
        });
      }
    }

    const showPomoWidget = isDeckBrowserWebview || isOverviewWebview;

    if (showPomoWidget) {
      if (!document.getElementById('lockin-pomo-widget')) {
        const widget = document.createElement('div');
        widget.id = 'lockin-pomo-widget';
        widget.className = 'lockin-pomo-floating-widget';
        widget.innerHTML = `
          <div class="lockin-pomo-dot-container">
            <span class="lockin-dot"></span>
          </div>
          <div id="lockin-pomo-timer-display">25:00</div>
          <button class="lockin-pomo-control-btn" id="lockin-pomo-toggle-btn" title="Start/Pause Timer">▶</button>
          <button class="lockin-pomo-control-btn" id="lockin-pomo-stop-btn" title="Stop & Reset Timer">■</button>
        `;
        document.body.appendChild(widget);

        // Bind widget controls
        document.getElementById('lockin-pomo-toggle-btn').addEventListener('click', togglePomodoro);
        document.getElementById('lockin-pomo-stop-btn').addEventListener('click', stopPomodoro);
      }
    }

    // Only create overlays in the main webview or deck list screens
    if (isMainWebview || isDeckBrowserWebview || isOverviewWebview) {
      // Custom answer buttons overlay (Ring of Buttons style)
      // Only render when the native bottom bar is hidden (otherwise we'd have duplicate UI),
      // the user wants the custom overlay, and we're not in shortcut-only mode.
      const ansBtnCond = isMainWebview
          && config.custom_answer_buttons
          && !config.hide_answer_buttons;
      if (ansBtnCond && !document.getElementById('lockin-answer-buttons')) {
        const wrap = document.createElement('div');
        wrap.id = 'lockin-answer-buttons';
        wrap.className = 'lockin-answer-buttons lockin-hidden';
        // Inline critical styles so the row is usable even if the cached CSS
        // hasn't refreshed yet. The external stylesheet layers polish on top.
        const bottomPos = config.bar_position === 'top' ? '20px' : '54px';
        wrap.style.cssText = [
          'position:fixed',
          'left:50%',
          `bottom:${bottomPos}`,
          'transform:translateX(-50%)',
          'display:flex',
          'gap:8px',
          'z-index:9998',
          'width:calc(100% - 40px)',
          'max-width:600px',
          'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif',
          'pointer-events:auto',
          'transition:opacity 300ms ease-out, transform 300ms cubic-bezier(0.22,0.61,0.36,1)'
        ].join(';');
        document.body.appendChild(wrap);
        // Buttons are populated by updateButtons() when Python sends interval
        // data via on_reviewer_did_show_answer. The click path uses Anki's
        // native pycmd("easeN") so it flows through _linkHandler -> _answerCard
        // exactly like the built-in review buttons.
        if (window.AnkiLockInAnswerMode) {
          setAnswerButtonsVisible(true);
        }
      }

      // Low health vignette overlay
      if (isMainWebview && !document.getElementById('lockin-low-health-overlay')) {
        const overlay = document.createElement('div');
        overlay.id = 'lockin-low-health-overlay';
        document.body.appendChild(overlay);
      }

      // Break screen overlay
      if (!document.getElementById('lockin-break-overlay')) {
        const breakOverlay = document.createElement('div');
        breakOverlay.id = 'lockin-break-overlay';
        breakOverlay.innerHTML = `
          <div class="lockin-break-content">
            <h1 class="lockin-break-title">Focus Rest Interval</h1>
            <p class="lockin-break-subtitle">Time to let your eyes rest and breathe deep</p>
            <div class="lockin-breathing-container">
              <div id="lockin-breathing-bubble">Breathe</div>
            </div>
            <div id="lockin-break-timer">05:00</div>
            <button class="lockin-btn" id="lockin-skip-break-btn">Skip Break & Study</button>
          </div>
        `;
        document.body.appendChild(breakOverlay);
        document.getElementById('lockin-skip-break-btn').addEventListener('click', endBreak);
      }
    }

    detectTheme();
    updateUI();
  }

  function updateUI() {
    const healthFill = document.getElementById('lockin-health-fill');
    const healthBadge = document.getElementById('lockin-health-badge');
    const streakBadge = document.getElementById('lockin-streak-badge');
    const pomoTimer = document.getElementById('lockin-pomo-timer');
    const pomoStatus = document.getElementById('lockin-pomo-status');
    const breakTimer = document.getElementById('lockin-break-timer');
    const root = document.documentElement;

    const widgetTimer = document.getElementById('lockin-pomo-timer-display');
    const widgetToggleBtn = document.getElementById('lockin-pomo-toggle-btn');
    const widgetDot = document.querySelector('#lockin-pomo-widget .lockin-dot');

    const minutes = Math.floor(pomoTimeLeft / 60);
    const seconds = Math.floor(pomoTimeLeft % 60);
    const timeStr = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

    if (widgetTimer) {
      widgetTimer.textContent = timeStr;
    }

    if (widgetToggleBtn) {
      if (pomoState === 'paused') {
        widgetToggleBtn.textContent = '▶';
        widgetToggleBtn.title = 'Start Timer';
      } else {
        widgetToggleBtn.textContent = '⏸';
        widgetToggleBtn.title = 'Pause Timer';
      }
    }

    if (widgetDot) {
      if (pomoState === 'break') {
        widgetDot.className = 'lockin-dot pomo-break';
      } else if (pomoState === 'paused') {
        widgetDot.className = 'lockin-dot pomo-paused';
      } else {
        widgetDot.className = 'lockin-dot';
      }
    }

    // 1. Health Bar & Warm-Up Grace status
    const isGracePeriod = (activeStudyDuration < config.grace_period_duration);
    const healthPercent = (health / config.health_max) * 100;
    
    if (healthFill) healthFill.style.width = `${healthPercent}%`;
    
    if (healthBadge) {
      if (isGracePeriod) {
        const warmSecondsLeft = Math.ceil(config.grace_period_duration - activeStudyDuration);
        healthBadge.textContent = `WARM (${warmSecondsLeft}s)`;
        healthBadge.classList.add('lockin-warmup-badge');
      } else {
        healthBadge.textContent = `HP ${Math.round(health)}`;
        healthBadge.classList.remove('lockin-warmup-badge');
      }
    }

    // Set colors dynamically based on health thresholds
    let activeColor = 'var(--color-green)';
    if (isGracePeriod) {
      activeColor = 'var(--color-yellow)'; // warm-up indicator color
      document.body.classList.remove('lockin-low-health');
    } else if (healthPercent < 25) {
      activeColor = 'var(--color-red)';
      document.body.classList.add('lockin-low-health');
    } else if (healthPercent < 55) {
      activeColor = 'var(--color-yellow)';
      document.body.classList.remove('lockin-low-health');
    } else {
      document.body.classList.remove('lockin-low-health');
    }
    root.style.setProperty('--health-bar-color', activeColor);

    // Dynamic Top/Bottom Layout positioning classes
    const bar = document.getElementById('lockin-bar');
    if (bar) {
      // Set positioning classes on both the bar and body
      if (config.bar_position === 'top') {
        bar.classList.add('lockin-bar-top');
        bar.classList.remove('lockin-bar-bottom');
        document.body.classList.add('lockin-bar-top');
        document.body.classList.remove('lockin-bar-bottom');
      } else {
        bar.classList.add('lockin-bar-bottom');
        bar.classList.remove('lockin-bar-top');
        document.body.classList.add('lockin-bar-bottom');
        document.body.classList.remove('lockin-bar-top');
      }

      // Shield active animations
      if (shieldTimeLeft > 0) {
        bar.classList.add('lockin-shield-active');
      } else {
        bar.classList.remove('lockin-shield-active');
      }
    }

    // 2. Focus Streak
    if (streakBadge) {
      if (config.adhd_boosters_streak_enabled && streak >= 3) {
        streakBadge.textContent = `⚡ STREAK x${streak}`;
        streakBadge.classList.add('streak-visible');
      } else {
        streakBadge.textContent = `⚡ STREAK x${streak}`;
        streakBadge.classList.remove('streak-visible');
      }
    }

    // 3. Pomodoro timers & Warning State (under 15s turns amber)
    if (pomoState === 'break') {
      if (breakTimer) breakTimer.textContent = timeStr;
    } else {
      if (pomoTimer) pomoTimer.textContent = timeStr;
      
      // Amber warning state 15s before break starts
      if (pomoStatus) {
        if (pomoTimeLeft <= 15) {
          pomoStatus.classList.add('pomo-warning');
        } else {
          pomoStatus.classList.remove('pomo-warning');
        }
      }
    }

    // Hide/Show Pomodoro status block based on configuration
    if (pomoStatus) {
      if (config.pomodoro_visible === false) {
        pomoStatus.style.display = 'none';
      } else {
        pomoStatus.style.display = 'flex';
      }
    }
  }

  // --- Streak & Shield Multipliers ---
  
  function getStreakMultiplier() {
    if (!config.adhd_boosters_streak_enabled) return 1.0;
    // Slows drain rate down with streak milestones
    if (streak >= 15) return 2.5;
    if (streak >= 10) return 2.0;
    if (streak >= 5) return 1.5;
    if (streak >= 3) return 1.25;
    return 1.0;
  }

  // --- Pomodoro Management ---
  
  function startBreak() {
    pomoState = 'break';
    pomoTimeLeft = config.pomodoro_break_duration * 60;
    breakStartTime = Date.now();
    playSound('break_start');

    // Show Full screen break overlay
    const overlay = document.getElementById('lockin-break-overlay');
    if (overlay) overlay.classList.add('overlay-open');

    // Change pomo dot indicator
    const dot = document.querySelector('#lockin-bar .lockin-dot');
    if (dot) {
      dot.className = 'lockin-dot pomo-break';
    }
    syncPomoState();
  }

  function endBreak() {
    pomoState = 'study';
    pomoTimeLeft = config.pomodoro_study_duration * 60;
    playSound('break_end');

    // Hide break overlay
    const overlay = document.getElementById('lockin-break-overlay');
    if (overlay) overlay.classList.remove('overlay-open');

    // Reset pomo dot indicator
    const dot = document.querySelector('#lockin-bar .lockin-dot');
    if (dot) {
      dot.className = 'lockin-dot';
    }
    
    // Auto heal a tiny bit after break to encourage starting
    health = Math.min(config.health_max, health + 15);
    syncPomoState();
    updateUI();
  }

  const BREATHING_PATTERNS = {
    'box': {
      name: 'Box Breathing (4-4-4-4)',
      phases: [
        { name: 'Breathe In', duration: 4, action: 'inhale' },
        { name: 'Hold', duration: 4, action: 'hold-full' },
        { name: 'Breathe Out', duration: 4, action: 'exhale' },
        { name: 'Hold', duration: 4, action: 'hold-empty' }
      ]
    },
    '478': {
      name: '4-7-8 Relaxation (4-7-8)',
      phases: [
        { name: 'Breathe In', duration: 4, action: 'inhale' },
        { name: 'Hold', duration: 7, action: 'hold-full' },
        { name: 'Breathe Out', duration: 8, action: 'exhale' }
      ]
    },
    'coherent': {
      name: 'Coherent Breathing (5-5)',
      phases: [
        { name: 'Breathe In', duration: 5, action: 'inhale' },
        { name: 'Breathe Out', duration: 5, action: 'exhale' }
      ]
    },
    'sigh': {
      name: 'Physiological Sigh (4-2-8)',
      phases: [
        { name: 'Double Inhale', duration: 4, action: 'inhale' },
        { name: 'Hold', duration: 2, action: 'hold-full' },
        { name: 'Sigh Out', duration: 8, action: 'exhale' }
      ]
    },
    'simple': {
      name: 'Equal Breathing (4-4)',
      phases: [
        { name: 'Breathe In', duration: 4, action: 'inhale' },
        { name: 'Breathe Out', duration: 4, action: 'exhale' }
      ]
    }
  };

  function updateBreathingVisualizer(now) {
    const breathingBubble = document.getElementById('lockin-breathing-bubble');
    if (!breathingBubble) return;

    const patternKey = config.breathing_pattern || 'box';
    const pattern = BREATHING_PATTERNS[patternKey] || BREATHING_PATTERNS['box'];
    
    let totalCycleTime = 0;
    for (let phase of pattern.phases) {
      totalCycleTime += phase.duration;
    }

    const elapsedInCycle = ((now - breakStartTime) / 1000) % totalCycleTime;

    let accumulated = 0;
    let activePhase = pattern.phases[0];
    let phaseProgress = 0;

    for (let phase of pattern.phases) {
      if (elapsedInCycle >= accumulated && elapsedInCycle < accumulated + phase.duration) {
        activePhase = phase;
        phaseProgress = (elapsedInCycle - accumulated) / phase.duration;
        break;
      }
      accumulated += phase.duration;
    }

    breathingBubble.textContent = activePhase.name;

    let scale = 1.0;
    if (activePhase.action === 'inhale') {
      scale = 1.0 + 0.8 * phaseProgress;
      breathingBubble.className = "breathe-in";
    } else if (activePhase.action === 'hold-full') {
      scale = 1.8;
      breathingBubble.className = "breathe-hold-full";
    } else if (activePhase.action === 'exhale') {
      scale = 1.8 - 0.8 * phaseProgress;
      breathingBubble.className = "breathe-out";
    } else if (activePhase.action === 'hold-empty') {
      scale = 1.0;
      breathingBubble.className = "breathe-hold-empty";
    }

    breathingBubble.style.transform = `scale(${scale})`;
  }

  // --- Focus Game Loop ---

  function tick() {
    const now = Date.now();
    const dt = (now - lastTickTime) / 1000;
    lastTickTime = now;

    // Handle game-over freezing pause
    if (gameOverFreeze) return;

    // Handle autopause when window loses focus
    const focusStateActive = isWindowFocused || !config.adhd_boosters_autopause_enabled;

    if (pomoState === 'study' && focusStateActive) {
      if (isMainWebview) {
        // Increment session active study time
        activeStudyDuration += dt;

        // 1. Manage Focus Shield
        if (shieldTimeLeft > 0) {
          shieldTimeLeft = Math.max(0, shieldTimeLeft - dt);
        }

        // 2. Grace Period & Sigmoid Health depletion
        const isGracePeriod = (activeStudyDuration < config.grace_period_duration);
        if (!isGracePeriod && shieldTimeLeft === 0) {
          const streakMultiplier = getStreakMultiplier();
          const cardTime = (now - cardStartTime) / 1000;
          
          // Sigmoid drain factor starts slow (10% drain) and accelerates as user stalls
          let drainFactor = 1.0;
          if (config.sigmoid_drain_enabled && !cardAnswered) {
            drainFactor = 0.1 + 1.9 / (1 + Math.exp(-0.35 * (cardTime - 6)));
          }
          
          health = Math.max(0, health - (config.health_drain_rate * dt * drainFactor) / streakMultiplier);
        }

        // 3. Heartbeat Audio tick for low health (<25%)
        const healthPercent = (health / config.health_max) * 100;
        if (!isGracePeriod && config.adhd_boosters_low_health_heartbeat && healthPercent < 25 && health > 0) {
          const heartbeatFreq = 300 + (healthPercent / 25) * 1100; // 300ms to 1400ms
          if (now - lastHeartbeatTime >= heartbeatFreq) {
            playSound('heartbeat');
            lastHeartbeatTime = now;
          }
        }

        // 4. Handle Game Over (health = 0)
        if (health === 0) {
          gameOverFreeze = true;
          playSound('gameover');
          
          const bar = document.getElementById('lockin-bar');
          if (bar) bar.classList.add('lockin-hit');

          if (gameOverTimeout) clearTimeout(gameOverTimeout);
          gameOverTimeout = setTimeout(() => {
            if (bar) bar.classList.remove('lockin-hit');
            
            if (!cardAnswered) {
              safePycmd("ease1");
              cardAnswered = true;
            }
            health = config.health_max * 0.5; // restore health to 50% grace
            activeStudyDuration = 0; // reset active study duration to avoid death spiral
            streak = 0; // break the focus streak
            shieldTimeLeft = 0;
            gameOverFreeze = false;
            gameOverTimeout = null;
            updateUI();
          }, 1500);
        }

        // 5. Question Elapsed, Sigmoid Resets, & Speed Focus alerts
        if (!cardAnswered) {
          const questionTime = (now - cardStartTime) / 1000;

          // Streak reset if idle exceeds user threshold
          if (config.adhd_boosters_streak_enabled && questionTime > config.streak_reset_idle_time) {
            if (streak > 0) {
              streak = 0;
              shieldTimeLeft = 0;
              updateUI();
            }
          }

          // Sound warning alert
          if (config.speed_focus_alert_delay > 0 && questionTime >= config.speed_focus_alert_delay && !alertSoundPlayed) {
            playSound('alert');
            alertSoundPlayed = true;
          }

          // Auto-Reveal Answer
          if (config.speed_focus_reveal_delay > 0 && questionTime >= config.speed_focus_reveal_delay) { // speed_focus_reveal_delay pycmd("ans")
            safePycmd("ans");
            cardAnswered = true; // prevent double triggers
          }

          // Auto-Grade Fail
          if (config.speed_focus_fail_delay > 0 && questionTime >= config.speed_focus_fail_delay && !cardAnswered) { // speed_focus_fail_delay pycmd("ease1")
            safePycmd("ease1");
            cardAnswered = true;
          }
        }
      }

      // 6. Study countdown
      pomoTimeLeft = Math.max(0, pomoTimeLeft - dt);
      if (pomoTimeLeft === 0) {
        startBreak();
      }
    } 
    else if (pomoState === 'break' && focusStateActive) {
      pomoTimeLeft = Math.max(0, pomoTimeLeft - dt);

      // Update dynamic breathing exercise
      updateBreathingVisualizer(now);

      if (pomoTimeLeft === 0) {
        endBreak();
      }
    }

    // Sync Pomodoro state to Python every second (1000ms)
    if (pomoState !== 'paused' && focusStateActive && (now - lastPomoSyncTime >= 1000)) {
      syncPomoState();
      lastPomoSyncTime = now;
    }

    detectTheme();
    updateUI();
  }

  function getAnswerButton(ease) {
    let btn = document.querySelector(`.lockin-ans-btn[data-ease="${ease}"]`);
    if (btn) return btn;

    const selector = `button[onclick*="ease${ease}"]`;
    btn = document.querySelector(selector);
    if (!btn && ease === 3) {
      btn = document.getElementById('defease');
    }
    if (!btn) {
      btn = document.querySelector(`input[onclick*="ease${ease}"]`);
    }
    return btn;
  }

  function setAnswerButtonsVisible(visible) {
    const wrap = document.getElementById('lockin-answer-buttons');
    if (!wrap) return;
    if (visible) {
      wrap.classList.remove('lockin-hidden');
      wrap.classList.add('lockin-shown');
      wrap.style.opacity = '1';
      wrap.style.pointerEvents = 'auto';
      wrap.style.transform = 'translateX(-50%) translateY(0)';
      document.body.classList.add('lockin-ans-shown');
    } else {
      wrap.classList.add('lockin-hidden');
      wrap.classList.remove('lockin-shown');
      wrap.style.opacity = '0';
      wrap.style.pointerEvents = 'none';
      wrap.style.transform = 'translateX(-50%) translateY(12px)';
      document.body.classList.remove('lockin-ans-shown');
    }
  }

  // --- Listeners & APIs called by Python ---

  const AnkiLockIn = {
    onShowQuestion: function () {
      initAudio();
      if (gameOverTimeout) {
        clearTimeout(gameOverTimeout);
        gameOverTimeout = null;
        gameOverFreeze = false;
      }
      cardStartTime = Date.now();
      cardAnswered = false;
      alertSoundPlayed = false;
      setAnswerButtonsVisible(false);
      updateUI();
    },

    onShowAnswer: function () {
      initAudio();
      cardAnswered = true;
      setAnswerButtonsVisible(true);
    },

    /**
     * Rebuild the custom answer button row from data pushed by Python.
     * buttonsData is an object keyed by numeric ease (string) with values:
     *   { label: "Again" | "Hard" | "Good" | "Easy", interval: "10d" | "" }
     * Buttons are wired to Anki's native pycmd("easeN") so the click goes
     * through _linkHandler -> _answerCard (same path as native buttons).
     */
    updateButtons: function (buttonsData) {
      safePycmd('lockin-debug:JS updateButtons called: ' + JSON.stringify(buttonsData));
      const wrap = document.getElementById('lockin-answer-buttons');
      if (!wrap) return;
      if (!buttonsData || typeof buttonsData !== 'object') return;

      // Clear existing buttons
      wrap.innerHTML = '';

      const colorMap = { 1: '#e74c3c', 2: '#f39c12', 3: '#2ecc71', 4: '#3498db' };
      const clsMap = {
        1: 'lockin-ans--again',
        2: 'lockin-ans--hard',
        3: 'lockin-ans--good',
        4: 'lockin-ans--easy'
      };

      Object.keys(buttonsData).forEach(function (easeKey) {
        const data = buttonsData[easeKey];
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'lockin-ans-btn ' + (clsMap[easeKey] || '');
        btn.setAttribute('data-ease', easeKey);
        btn.title = data.label || '';
        btn.style.cssText =
          'flex:1 1 0;min-width:80px;max-width:150px;' +
          'display:flex;align-items:center;justify-content:center;' +
          'padding:10px 16px;border:none;border-radius:10px;' +
          'background:' + (colorMap[easeKey] || '#888') + ';' +
          'color:#fff;cursor:pointer;' +
          'box-shadow:0 4px 14px rgba(0,0,0,0.25);' +
          'font:inherit;font-size:15px;font-weight:700;line-height:1;' +
          'outline:none;-webkit-appearance:none;appearance:none;' +
          'text-shadow:0 1px 2px rgba(0,0,0,0.25);';
        btn.innerHTML =
          '<span class="lockin-ans-interval" data-interval-for="' + easeKey + '">' +
          (data.interval || '—') + '</span>';
        // Buttons with no computed interval (learning cards) are disabled
        if (!data.interval) {
          btn.classList.add('lockin-ans-unsupported');
          btn.disabled = true;
          btn.setAttribute('aria-disabled', 'true');
        }
        btn.addEventListener('click', function () {
          btn.classList.add('lockin-ans-picked');
          setTimeout(function () { btn.classList.remove('lockin-ans-picked'); }, 350);
          if (typeof pycmd === 'function') { pycmd('ease' + easeKey); }
          else if (typeof window.pycmd === 'function') { window.pycmd('ease' + easeKey); }
        });
        wrap.appendChild(btn);
      });

      // Anki's main webview persists across card transitions, so the row
      // is naturally visible from the moment it is populated. If we land
      // here while in answer mode, make sure it isn't stuck hidden.
      if (window.AnkiLockInAnswerMode) {
        setAnswerButtonsVisible(true);
      }
    },

    onAnswerCard: function (ease, label) {
      initAudio();
      if (gameOverTimeout) {
        clearTimeout(gameOverTimeout);
        gameOverTimeout = null;
        gameOverFreeze = false;
      }

      const answerSpeed = (Date.now() - cardStartTime) / 1000;
      statsCards++;

      // 1. Calculate Health Change. Prefer the semantic label pushed from
      // Python so the mapping stays correct when the user runs a 2/3-button
      // deck (where ease 2 means "Good" rather than "Hard"). Fall back to
      // numeric ease for backwards compatibility.
      let healthDelta = 0;
      switch (label) {
        case 'again': healthDelta = config.health_heal_again; break;
        case 'hard':  healthDelta = config.health_heal_hard;  break;
        case 'good':  healthDelta = config.health_heal_good;  break;
        case 'easy':  healthDelta = config.health_heal_easy;  break;
        default:
          if (ease === 1) healthDelta = config.health_heal_again;
          else if (ease === 2) healthDelta = config.health_heal_hard;
          else if (ease === 3) healthDelta = config.health_heal_good;
          else if (ease === 4) healthDelta = config.health_heal_easy;
      }

      // Add a bonus healing points if they answer very fast (<3.5 seconds) and correct
      if (ease > 1 && answerSpeed < 3.5) {
        healthDelta += 5;
      }

      // Track undo details
      let streakDelta = 0;
      let shieldTimeAdded = 0;

      // Heal/damage bar
      const oldHealth = health;
      health = Math.max(0, Math.min(config.health_max, health + healthDelta));
      const actualHealthDelta = health - oldHealth;

      if (ease > 1) {
        statsCorrect++;
      }

      // Visual pulse response on bar when hit
      const bar = document.getElementById('lockin-bar');
      if (bar) {
        bar.classList.add('lockin-hit');
        setTimeout(() => bar.classList.remove('lockin-hit'), 300);
      }

      // 2. Manage Focus Streaks
      if (config.adhd_boosters_streak_enabled) {
        const streakSpeedThreshold = config.streak_speed_threshold || 5.0;
        if (ease > 1 && answerSpeed <= streakSpeedThreshold) {
          streakDelta = 1;
          streak++;
          
          if (streak > statsMaxStreak) {
            statsMaxStreak = streak;
          }

          // Animate streak badge
          const streakBadge = document.getElementById('lockin-streak-badge');
          if (streakBadge) {
            streakBadge.classList.add('streak-gaining');
            setTimeout(() => streakBadge.classList.remove('streak-gaining'), 400);
          }

          // Trigger Success Chime on streak milestones
          if (streak % 5 === 0) {
            if (config.adhd_boosters_shield_enabled) {
              shieldTimeLeft = 12.0; // 12 seconds shield
              shieldTimeAdded = 12.0;
              statsShields++;
              playSound('shield_up');
            } else {
              playSound('success');
            }
          }
        } else if (ease === 1) {
          // "Again" does NOT break the streak! The streak survives.
        }
      }

      // Store this action for undo
      lastAction = {
        healthDelta: actualHealthDelta,
        streakDelta: streakDelta,
        isCorrect: ease > 1,
        shieldTimeAdded: shieldTimeAdded
      };

      sendStatsToPython();
      updateUI();
    },

    updateConfig: function (newConfig) {
      config = newConfig;
      health = Math.min(health, config.health_max);
      if (pomoState === 'study') {
        pomoTimeLeft = Math.min(pomoTimeLeft, config.pomodoro_study_duration * 60);
      } else if (pomoState === 'break') {
        pomoTimeLeft = Math.min(pomoTimeLeft, config.pomodoro_break_duration * 60);
      }
      
      // Clean up existing elements and rebuild based on new config position
      const bar = document.getElementById('lockin-bar');
      if (bar) bar.remove();
      const widget = document.getElementById('lockin-pomo-widget');
      if (widget) widget.remove();
      const overlay = document.getElementById('lockin-break-overlay');
      if (overlay) overlay.remove();
      const lowHealth = document.getElementById('lockin-low-health-overlay');
      if (lowHealth) lowHealth.remove();
      const ansBtns = document.getElementById('lockin-answer-buttons');
      if (ansBtns) ansBtns.remove();
      
      document.body.classList.remove('lockin-active', 'lockin-bar-top', 'lockin-bar-bottom', 'lockin-main', 'lockin-bottom-webview', 'lockin-deckbrowser', 'lockin-overview');
      
      createUI();
    },

    animateButton: function (data) {
      let btn = null;
      if (data.isShowAnswer) {
        btn = document.getElementById('ansbut') || document.querySelector('button[onclick*="ans"]') || document.querySelector('input[onclick*="ans"]');
      } else if (data.ease) {
        btn = getAnswerButton(data.ease);
      }

      if (btn) {
        if (btn.classList.contains('lockin-ans-btn')) {
          btn.classList.add('lockin-ans-picked');
          setTimeout(() => {
            btn.classList.remove('lockin-ans-picked');
          }, 350);
        } else {
          btn.classList.add('lockin-btn-feedback');
          setTimeout(() => {
            btn.classList.remove('lockin-btn-feedback');
          }, 150);
        }
      }
    },

    onUndo: function () {
      // Reset answer button state — the undone card returns to its prior phase
      setAnswerButtonsVisible(false);
      if (lastAction) {
        // Revert stats
        statsCards = Math.max(0, statsCards - 1);
        if (lastAction.isCorrect) {
          statsCorrect = Math.max(0, statsCorrect - 1);
        }

        // Revert health
        health = Math.max(0, Math.min(config.health_max, health - lastAction.healthDelta));

        // Revert streak
        streak = Math.max(0, streak - lastAction.streakDelta);

        // Revert shield
        if (lastAction.shieldTimeAdded > 0) {
          shieldTimeLeft = Math.max(0, shieldTimeLeft - lastAction.shieldTimeAdded);
          statsShields = Math.max(0, statsShields - 1);
        }

        lastAction = null; // Clear so you can't double undo
        sendStatsToPython();
        updateUI();
      }
    },

    createUI: createUI,
    resetSession: function () {
      health = config.health_max;
      streak = 0;
      shieldTimeLeft = 0;
      cardAnswered = false;
      alertSoundPlayed = false;
      statsCards = 0;
      statsCorrect = 0;
      statsMaxStreak = 0;
      statsShields = 0;
      activeStudyDuration = 0;
      gameOverFreeze = false;
      if (gameOverTimeout) {
        clearTimeout(gameOverTimeout);
        gameOverTimeout = null;
      }
      
      // Load Pomodoro state from injected global variables if available
      if (window.AnkiLockInPomoState && window.AnkiLockInPomoState.time_left !== -1) {
        pomoState = window.AnkiLockInPomoState.state;
        pomoTimeLeft = window.AnkiLockInPomoState.time_left;
      } else {
        pomoState = 'study';
        pomoTimeLeft = config.pomodoro_study_duration * 60;
      }
    }
  };

  // Bind window blur/focus event handlers for Auto Pause
  window.addEventListener('blur', () => {
    isWindowFocused = false;
  });

  window.addEventListener('focus', () => {
    isWindowFocused = true;
    lastTickTime = Date.now();
  });

  // Listen to keyboard shortcuts in main webview. The actual answer command
  // (pycmd("easeN")) is handled by Anki's Qt-level QShortcut. This handler
  // is a FALLBACK visual only: it adds the "picked" highlight on the
  // matching custom button, but the answer itself goes through Anki's
  // native path (or the reviewer's _linkHandler for mouse clicks). The
  // primary animation now comes from the Python reviewer_will_answer_card
  // hook, so this is best-effort and harmless if it never fires.
  if (isMainWebview) {
    document.addEventListener('keydown', (e) => {
      // Ignore if typing in text inputs
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) {
        return;
      }

      let ease = null;

      if (e.key === '1') {
        ease = 1;
      } else if (e.key === '2') {
        ease = 2;
      } else if (e.key === '3') {
        ease = 3;
      } else if (e.key === '4') {
        ease = 4;
      } else if (e.key === ' ' || e.key === 'Enter') {
        if (cardAnswered) {
          ease = 3; // Treat as default Good ease for visual highlight
        }
      }

      if (ease !== null && cardAnswered) {
        const btn = document.querySelector(`.lockin-ans-btn[data-ease="${ease}"]`);
        if (btn) {
          btn.classList.add('lockin-ans-picked');
          setTimeout(() => btn.classList.remove('lockin-ans-picked'), 350);
        }
      }
    });
  }

  // Export module globally
  window.AnkiLockIn = AnkiLockIn;

  // Initialize UI & Start Timer Loop
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      createUI();
      lastTickTime = Date.now();
      if (isMainWebview || isDeckBrowserWebview || isOverviewWebview) {
        setInterval(tick, 100);
      }
    });
  } else {
    createUI();
    lastTickTime = Date.now();
    if (isMainWebview || isDeckBrowserWebview || isOverviewWebview) {
      setInterval(tick, 100);
    }
  }
})();
