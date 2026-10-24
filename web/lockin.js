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
    ui_language: "de",
    collectibles_enabled: true,
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
    no_distraction_hide_menu_bar: true,
    no_distraction_hide_top_toolbar: true,
    no_distraction_hide_bottom_bar: false,
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
    hide_answer_buttons: false,
    streak_speed_threshold: 5.0,
    breathing_pattern: "box"
  };

  // German is the safe default for this add-on. Python may provide a locale in
  // the collection snapshot (preferred) or through the regular add-on config.
  const UI_STRINGS = {
    de: {
      health: 'LP {value}',
      warmup: 'AUFWÄRMEN ({seconds}s)',
      streak: '⚡ SERIE ×{value}',
      exit: 'Beenden',
      exit_title: 'Lernmodus beenden und zur Stapelübersicht zurückkehren',
      pause_timer: 'Timer pausieren',
      resume_timer: 'Timer fortsetzen',
      stop_timer: 'Timer stoppen und zurücksetzen',
      break_title: 'Fokuspause',
      break_subtitle: 'Zeit, die Augen zu entspannen und ruhig zu atmen.',
      breathe: 'Atmen',
      breathe_in: 'Einatmen',
      breathe_out: 'Ausatmen',
      breathe_hold: 'Halten',
      breathe_double: 'Doppelt einatmen',
      breathe_sigh: 'Seufzend ausatmen',
      skip_break: 'Pause überspringen und weiterlernen',
      answer_again: 'Nochmal',
      answer_hard: 'Schwer',
      answer_good: 'Gut',
      answer_easy: 'Einfach',
      show_answer: 'Antwort zeigen',
      collection: 'Katzensammlung',
      collection_open: 'Katzensammlung öffnen. Ausgewählt: {name}. {unlocked} von {total} Katzen entdeckt.{next}',
      collection_count: '{unlocked}/{total} Katzen',
      collection_empty: 'Noch keine Katze ausgewählt',
      collection_complete: 'Sammlung vollständig',
      collection_next: ' Nächstes Ziel: {label}.',
      new_cat: 'Neue Katze entdeckt!',
      new_cats: '{count} neue Katzen entdeckt!',
      unlock_reason: 'Neues Sammlungsziel erreicht',
      view_collection: 'Ansehen',
      close: 'Schließen'
    },
    en: {
      health: 'HP {value}',
      warmup: 'WARM UP ({seconds}s)',
      streak: '⚡ STREAK ×{value}',
      exit: 'Exit',
      exit_title: 'Exit review and return to decks',
      pause_timer: 'Pause timer',
      resume_timer: 'Resume timer',
      stop_timer: 'Stop and reset timer',
      break_title: 'Focus break',
      break_subtitle: 'Let your eyes rest and breathe slowly.',
      breathe: 'Breathe',
      breathe_in: 'Breathe in',
      breathe_out: 'Breathe out',
      breathe_hold: 'Hold',
      breathe_double: 'Double inhale',
      breathe_sigh: 'Sigh out',
      skip_break: 'Skip break and continue studying',
      answer_again: 'Again',
      answer_hard: 'Hard',
      answer_good: 'Good',
      answer_easy: 'Easy',
      show_answer: 'Show Answer',
      collection: 'Cat collection',
      collection_open: 'Open cat collection. Equipped: {name}. {unlocked} of {total} cats discovered.{next}',
      collection_count: '{unlocked}/{total} cats',
      collection_empty: 'No cat equipped yet',
      collection_complete: 'Collection complete',
      collection_next: ' Next goal: {label}.',
      new_cat: 'New cat discovered!',
      new_cats: '{count} new cats discovered!',
      unlock_reason: 'New collection goal reached',
      view_collection: 'View',
      close: 'Close'
    }
  };

  let collectionSnapshot = window.AnkiLockInCollectionSnapshot || null;

  function getLocale() {
    const requested = (collectionSnapshot && collectionSnapshot.locale) ||
      config.ui_language || config.language || 'de';
    return String(requested).toLowerCase().startsWith('en') ? 'en' : 'de';
  }

  function t(key, values) {
    const locale = getLocale();
    let value = (UI_STRINGS[locale] && UI_STRINGS[locale][key]) ||
      UI_STRINGS.de[key] || UI_STRINGS.en[key] || key;
    Object.keys(values || {}).forEach(function (name) {
      value = value.replace(new RegExp('\\{' + name + '\\}', 'g'), String(values[name]));
    });
    return value;
  }

  const reducedMotionQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  function prefersReducedMotion() {
    return !!(reducedMotionQuery && reducedMotionQuery.matches);
  }

  // State for custom answer buttons overlay is now pushed from Python via
  // window.AnkiLockIn.updateButtons(buttonsData) which rebuilds the row.
  // Intervals live only in the DOM; no client-side cache is needed.

  // Safe python command bridge caller
  function safePycmd(message) {
    if (typeof pycmd !== 'undefined') {
      pycmd(message);
    } else if (typeof window.pycmd !== 'undefined') {
      window.pycmd(message);
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
      // Python knows the actual context. Generic card-template IDs such as
      // #buttons or #wrapper must not override that explicit classification.
      isBottomWebview = (window.AnkiLockInWebviewKind === 'bottom');
      isMainWebview = (window.AnkiLockInWebviewKind === 'main');
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
  let pomoPhaseBeforePause = 'study';
  let pomoTimeLeft = config.pomodoro_study_duration * 60;
  if (window.AnkiLockInPomoState && window.AnkiLockInPomoState.time_left !== -1) {
    pomoState = window.AnkiLockInPomoState.state;
    pomoTimeLeft = window.AnkiLockInPomoState.time_left;
    pomoPhaseBeforePause = window.AnkiLockInPomoState.pre_pause_state === 'break' ? 'break' :
      (pomoState === 'break' ? 'break' : 'study');
  }
  
  let cardStartTime = Date.now();
  let activeQuestionTime = 0;
  let reviewPhase = window.AnkiLockInAnswerMode ? 'answer' : 'question';
  let questionResponseTime = null;
  let revealRequested = false;
  let alertSoundPlayed = false;
  let latestButtonsPayload = null;
  
  let lastTickTime = Date.now();
  let lastHeartbeatTime = 0;
  let lastPomoSyncTime = 0;
  let breakStartTime = 0;
  let isWindowFocused = true;
  let gameOverFreeze = false;
  let gameOverTimeout = null;
  // Anki exposes an exact undo counter for each answer. Keep full pre-answer
  // and post-answer snapshots in LIFO order so repeated Undo/Redo restores the
  // matching state and unrelated Anki undo operations cannot affect the focus game.
  const answerActionsByUndoCounter = new Map();
  const answerUndoOrder = [];
  const undoneActionsByUndoCounter = new Map();
  const undoneRedoOrder = [];

  // Session Statistics
  let statsCards = 0;
  let statsCorrect = 0;
  let statsMaxStreak = 0;
  let statsShields = 0;
  let statsStudyTime = 0;
  let activeStudyDuration = 0; // accumulated focus study time in seconds
  let cycleStudyStart = 0; // activeStudyDuration offset at start of current pomodoro cycle

  // Accessible unlock notifications are deliberately ephemeral client state;
  // collection ownership and unseen state remain Python-authoritative.
  let pendingUnlockEvents = [];
  let unlockToastQueue = [];
  let unlockBatchTimer = null;
  let unlockDismissTimer = null;
  let unlockToastActive = false;

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
    safePycmd("lockin-pomo-sync:" + JSON.stringify({
      state: pomoState,
      time_left: pomoTimeLeft,
      pre_pause_state: pomoPhaseBeforePause
    }));
  }

  function pausePomodoro() {
    initAudio();
    if (pomoState === 'study' || pomoState === 'break') {
      pomoPhaseBeforePause = pomoState;
      pomoState = 'paused';
      syncPomoState();
      updateUI();
    }
  }

  function resumePomodoro() {
    initAudio();
    if (pomoState === 'paused') {
      pomoState = pomoPhaseBeforePause === 'break' ? 'break' : 'study';
      syncPomoState();
      updateUI();
    }
  }

  function stopPomodoro() {
    initAudio();
    pomoPhaseBeforePause = 'study';
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

  function storeAnswerAction(undoCounter, preSnapshot) {
    if (undoCounter === null || undoCounter === undefined) return;
    undoneActionsByUndoCounter.clear();
    undoneRedoOrder.length = 0;

    // A counter is expected to be unique within an Anki runtime. If the same
    // exact value is ever delivered twice, keep only its newest snapshot so
    // the LIFO stack and map cannot diverge.
    if (answerActionsByUndoCounter.has(undoCounter)) {
      for (let index = answerUndoOrder.length - 1; index >= 0; index--) {
        if (Object.is(answerUndoOrder[index], undoCounter)) {
          answerUndoOrder.splice(index, 1);
          break;
        }
      }
    }
    answerActionsByUndoCounter.set(undoCounter, preSnapshot);
    answerUndoOrder.push(undoCounter);
  }

  function takeLatestAnswerAction(undoCounter) {
    if (undoCounter === null || undoCounter === undefined) return null;
    if (!answerActionsByUndoCounter.has(undoCounter)) return null;

    const latestCounter = answerUndoOrder[answerUndoOrder.length - 1];
    if (!Object.is(latestCounter, undoCounter)) {
      // Anki undo is LIFO. Refuse an out-of-order matching counter rather than
      // restoring a state snapshot over newer answers.
      return null;
    }

    answerUndoOrder.pop();
    const action = answerActionsByUndoCounter.get(undoCounter);
    answerActionsByUndoCounter.delete(undoCounter);
    if (action) {
      undoneActionsByUndoCounter.set(undoCounter, action);
      undoneRedoOrder.push(undoCounter);
      return action;
    }
    return null;
  }

  function takeLatestRedoAction(undoCounter) {
    if (undoCounter === null || undoCounter === undefined) return null;
    if (!undoneActionsByUndoCounter.has(undoCounter)) return null;

    const latestCounter = undoneRedoOrder[undoneRedoOrder.length - 1];
    if (!Object.is(latestCounter, undoCounter)) {
      return null;
    }

    undoneRedoOrder.pop();
    const action = undoneActionsByUndoCounter.get(undoCounter);
    undoneActionsByUndoCounter.delete(undoCounter);
    if (action) {
      answerActionsByUndoCounter.set(undoCounter, action);
      answerUndoOrder.push(undoCounter);
      return action.post || action;
    }
    return null;
  }

  function clearAnswerActions() {
    answerActionsByUndoCounter.clear();
    answerUndoOrder.length = 0;
    undoneActionsByUndoCounter.clear();
    undoneRedoOrder.length = 0;
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

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function catAssetUrl(cat) {
    if (!cat) return '';
    return cat.asset_url || cat.image_url || cat.image || cat.asset || '';
  }

  function catDisplayName(cat) {
    if (!cat) return t('collection_empty');
    return cat.localized_name || cat.name || cat.title || t('collection_empty');
  }

  function setCatImage(img, fallback, url) {
    if (!img || !fallback) return;
    img.onload = function () {
      img.hidden = false;
      fallback.hidden = true;
    };
    img.onerror = function () {
      img.hidden = true;
      fallback.hidden = false;
      img.removeAttribute('src');
    };
    if (url) {
      fallback.hidden = true;
      img.hidden = false;
      img.src = url;
    } else {
      img.hidden = true;
      img.removeAttribute('src');
      fallback.hidden = false;
    }
  }

  function nextMilestoneText(snapshot) {
    const milestone = snapshot && snapshot.next_milestone;
    if (!milestone) {
      const unlocked = Math.max(0, finiteNumber(snapshot && snapshot.unlocked_count, 0));
      const total = Math.max(1, finiteNumber(snapshot && snapshot.total_count, 50));
      return unlocked >= total ? t('collection_complete') : t('collection_empty');
    }
    let label = milestone.localized_label || milestone.label || milestone.progress_label ||
      milestone.criterion_label || milestone.name || '';
    const current = finiteNumber(milestone.current, null);
    const target = finiteNumber(milestone.target, null);
    if (current !== null && target !== null && target > 0) {
      label = label ? `${label} · ${Math.max(0, current)}/${target}` : `${Math.max(0, current)}/${target}`;
    }
    return label || t('collection_complete');
  }

  function updateCollectionUI() {
    const chip = document.getElementById('lockin-cat-chip');
    if (!chip) return;

    const snapshot = collectionSnapshot || {};
    const enabled = Boolean(config.collectibles_enabled && snapshot.enabled !== false);
    chip.hidden = !enabled;
    if (!enabled) {
      if (unlockToastActive) dismissUnlockToast();
      unlockToastQueue.length = 0;
      pendingUnlockEvents.length = 0;
      return;
    }

    const unlocked = Math.max(0, finiteNumber(snapshot.unlocked_count, 0));
    const total = Math.max(1, finiteNumber(snapshot.total_count, 50));
    const equipped = snapshot.equipped || snapshot.equipped_cat || null;
    const name = catDisplayName(equipped);
    const milestoneText = nextMilestoneText(snapshot);
    const unseen = Math.max(0, finiteNumber(snapshot.unseen_count, 0));

    const avatar = document.getElementById('lockin-cat-avatar');
    const avatarFallback = document.getElementById('lockin-cat-avatar-fallback');
    setCatImage(avatar, avatarFallback, catAssetUrl(equipped));

    const avatarShell = document.getElementById('lockin-cat-avatar-shell');
    if (avatarShell) {
      const degrees = Math.max(0, Math.min(360, (unlocked / total) * 360));
      avatarShell.style.setProperty('--lockin-cat-progress', `${degrees}deg`);
    }

    const count = document.getElementById('lockin-cat-count');
    if (count) count.textContent = t('collection_count', { unlocked: unlocked, total: total });
    const next = document.getElementById('lockin-cat-next');
    if (next) next.textContent = milestoneText;
    const unseenBadge = document.getElementById('lockin-cat-unseen');
    if (unseenBadge) {
      unseenBadge.hidden = unseen < 1;
      unseenBadge.textContent = unseen > 99 ? '99+' : String(unseen);
    }

    const nextAria = milestoneText ? t('collection_next', { label: milestoneText }) : '';
    chip.setAttribute('aria-label', t('collection_open', {
      name: name,
      unlocked: unlocked,
      total: total,
      next: nextAria
    }));
    chip.title = `${name} — ${unlocked}/${total}. ${milestoneText}`;
    chip.lang = getLocale();
  }

  function updateStaticLocalizedUI() {
    const bar = document.getElementById('lockin-bar');
    if (bar) bar.lang = getLocale();
    const exit = document.getElementById('lockin-exit-btn');
    if (exit) {
      exit.title = t('exit_title');
      exit.setAttribute('aria-label', t('exit_title'));
      const label = exit.querySelector('.lockin-exit-label');
      if (label) label.textContent = t('exit');
    }
    [
      ['lockin-pomo-pause-btn', 'pause_timer'],
      ['lockin-pomo-resume-btn', 'resume_timer'],
      ['lockin-pomo-stop-btn', 'stop_timer'],
      ['lockin-break-pause-btn', 'pause_timer'],
      ['lockin-break-resume-btn', 'resume_timer'],
      ['lockin-break-reset-btn', 'stop_timer']
    ].forEach(function (entry) {
      const button = document.getElementById(entry[0]);
      if (button) {
        button.title = t(entry[1]);
        button.setAttribute('aria-label', t(entry[1]));
        if (entry[0].startsWith('lockin-break-')) button.textContent = t(entry[1]);
      }
    });
    const breakOverlay = document.getElementById('lockin-break-overlay');
    if (breakOverlay) breakOverlay.lang = getLocale();
    const breakTitle = document.getElementById('lockin-break-title');
    if (breakTitle) breakTitle.textContent = t('break_title');
    const breakSubtitle = document.querySelector('#lockin-break-overlay .lockin-break-subtitle');
    if (breakSubtitle) breakSubtitle.textContent = t('break_subtitle');
    const breathing = document.getElementById('lockin-breathing-bubble');
    if (breathing && pomoState !== 'break') breathing.textContent = t('breathe');
    const skip = document.getElementById('lockin-skip-break-btn');
    if (skip) skip.textContent = t('skip_break');
    const region = document.getElementById('lockin-cat-toast-region');
    if (region) region.lang = getLocale();
  }

  function createUnlockToastRegion() {
    if (!isMainWebview || document.getElementById('lockin-cat-toast-region')) return;
    const region = document.createElement('div');
    region.id = 'lockin-cat-toast-region';
    region.setAttribute('role', 'status');
    region.setAttribute('aria-live', 'polite');
    region.setAttribute('aria-atomic', 'true');
    region.lang = getLocale();
    document.body.appendChild(region);
  }

  function appendToastCatImage(parent, cat) {
    const frame = document.createElement('span');
    frame.className = 'lockin-cat-toast-thumb';
    const img = document.createElement('img');
    img.alt = '';
    img.decoding = 'async';
    const fallback = document.createElement('span');
    fallback.className = 'lockin-cat-toast-thumb-fallback';
    fallback.textContent = '🐈';
    fallback.setAttribute('aria-hidden', 'true');
    frame.appendChild(img);
    frame.appendChild(fallback);
    parent.appendChild(frame);
    setCatImage(img, fallback, catAssetUrl(cat));
  }

  function scheduleUnlockDismiss() {
    if (unlockDismissTimer) clearTimeout(unlockDismissTimer);
    unlockDismissTimer = setTimeout(dismissUnlockToast, 8000);
  }

  function dismissUnlockToast() {
    if (!unlockToastActive) return;
    if (unlockDismissTimer) {
      clearTimeout(unlockDismissTimer);
      unlockDismissTimer = null;
    }
    const region = document.getElementById('lockin-cat-toast-region');
    const toast = region && region.querySelector('.lockin-cat-toast');
    if (toast) toast.classList.remove('lockin-cat-toast--visible');
    const delay = prefersReducedMotion() ? 0 : 180;
    setTimeout(function () {
      if (toast) toast.remove();
      unlockToastActive = false;
      renderNextUnlockToast();
    }, delay);
  }

  function renderNextUnlockToast() {
    if (unlockToastActive || unlockToastQueue.length === 0) return;
    if (!config.collectibles_enabled || (collectionSnapshot && collectionSnapshot.enabled === false)) {
      unlockToastQueue.length = 0;
      return;
    }
    const breakOverlay = document.getElementById('lockin-break-overlay');
    if (breakOverlay && breakOverlay.classList.contains('overlay-open')) {
      setTimeout(renderNextUnlockToast, 500);
      return;
    }

    const region = document.getElementById('lockin-cat-toast-region');
    if (!region) return;
    const events = unlockToastQueue.shift();
    if (!events || events.length === 0) return;
    unlockToastActive = true;

    // Acknowledge delivery to Python so unshown rewards are tracked accurately across review teardown
    safePycmd('lockin-unlock-delivered:' + JSON.stringify({ cat_ids: events.map(function (e) { return e.id; }) }));

    const toast = document.createElement('div');
    toast.className = 'lockin-cat-toast';
    toast.lang = getLocale();

    const thumbs = document.createElement('div');
    thumbs.className = 'lockin-cat-toast-thumbs';
    events.slice(0, 3).forEach(function (event) {
      appendToastCatImage(thumbs, event);
    });

    const copy = document.createElement('div');
    copy.className = 'lockin-cat-toast-copy';
    const title = document.createElement('div');
    title.className = 'lockin-cat-toast-title';
    title.textContent = events.length === 1 ? t('new_cat') : t('new_cats', { count: events.length });
    const detail = document.createElement('div');
    detail.className = 'lockin-cat-toast-detail';
    if (events.length === 1) {
      const event = events[0];
      const reason = event.localized_reason || event.reason || event.unlock_reason || t('unlock_reason');
      detail.textContent = `${catDisplayName(event)} — ${reason}`;
    } else {
      const names = events.slice(0, 3).map(catDisplayName);
      detail.textContent = names.join(', ') + (events.length > 3 ? ` +${events.length - 3}` : '');
    }
    copy.appendChild(title);
    copy.appendChild(detail);

    const actions = document.createElement('div');
    actions.className = 'lockin-cat-toast-actions';
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'lockin-cat-toast-view';
    view.textContent = t('view_collection');
    view.addEventListener('click', function () {
      const catId = (events && events[0] && events[0].id) || '';
      safePycmd(catId ? 'lockin-open-cat-collection:' + catId : 'lockin-open-cat-collection');
      dismissUnlockToast();
    });
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'lockin-cat-toast-close';
    close.setAttribute('aria-label', t('close'));
    close.textContent = '×';
    close.addEventListener('click', dismissUnlockToast);
    actions.appendChild(view);
    actions.appendChild(close);

    toast.appendChild(thumbs);
    toast.appendChild(copy);
    toast.appendChild(actions);
    toast.addEventListener('mouseenter', function () {
      if (unlockDismissTimer) clearTimeout(unlockDismissTimer);
    });
    toast.addEventListener('mouseleave', scheduleUnlockDismiss);
    toast.addEventListener('focusin', function () {
      if (unlockDismissTimer) clearTimeout(unlockDismissTimer);
    });
    toast.addEventListener('focusout', function (event) {
      if (!toast.contains(event.relatedTarget)) scheduleUnlockDismiss();
    });
    region.appendChild(toast);
    requestAnimationFrame(function () {
      toast.classList.add('lockin-cat-toast--visible');
    });
    scheduleUnlockDismiss();
  }

  function flushPendingUnlocks() {
    unlockBatchTimer = null;
    if (pendingUnlockEvents.length === 0) return;
    unlockToastQueue.push(pendingUnlockEvents.splice(0));
    renderNextUnlockToast();
  }

  function queueCatUnlocks(events) {
    if (!config.collectibles_enabled || (collectionSnapshot && collectionSnapshot.enabled === false)) return;
    const items = Array.isArray(events) ? events : [events];
    items.filter(function (item) {
      return item && typeof item === 'object';
    }).forEach(function (item) {
      pendingUnlockEvents.push(item);
    });
    if (pendingUnlockEvents.length === 0) return;
    if (unlockBatchTimer) clearTimeout(unlockBatchTimer);
    unlockBatchTimer = setTimeout(flushPendingUnlocks, 180);
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
        bar.lang = getLocale();
        bar.innerHTML = `
          <div class="lockin-left-section">
            <div id="lockin-health-badge">${t('health', { value: 100 })}</div>
            <div class="lockin-health-bar-container" role="progressbar" aria-valuemin="0" aria-valuemax="${config.health_max}" aria-valuenow="${Math.round(health)}">
              <div id="lockin-health-fill" style="width: 100%"></div>
              <div id="lockin-shield-overlay"></div>
            </div>
          </div>
          <div class="lockin-center-section">
            <button type="button" id="lockin-cat-chip" aria-haspopup="dialog">
              <span id="lockin-cat-avatar-shell" class="lockin-cat-avatar-shell" aria-hidden="true">
                <img id="lockin-cat-avatar" alt="" decoding="async">
                <span id="lockin-cat-avatar-fallback" class="lockin-cat-avatar-fallback">🐈</span>
              </span>
              <span class="lockin-cat-chip-copy">
                <span id="lockin-cat-count">${t('collection_count', { unlocked: 0, total: 50 })}</span>
                <span id="lockin-cat-next">${t('collection_empty')}</span>
              </span>
              <span id="lockin-cat-unseen" class="lockin-cat-unseen" hidden></span>
            </button>
            <div id="lockin-streak-badge" aria-live="off">${t('streak', { value: 0 })}</div>
          </div>
          <div class="lockin-right-section">
            <div id="lockin-pomo-status">
              <span class="lockin-dot"></span>
              <span id="lockin-pomo-timer">25:00</span>
              <span class="lockin-pomo-controls">
                <button type="button" class="lockin-pomo-btn" id="lockin-pomo-pause-btn" title="${t('pause_timer')}" aria-label="${t('pause_timer')}">⏸</button>
                <button type="button" class="lockin-pomo-btn" id="lockin-pomo-resume-btn" title="${t('resume_timer')}" aria-label="${t('resume_timer')}" style="display:none;">▶</button>
                <button type="button" class="lockin-pomo-btn" id="lockin-pomo-stop-btn" title="${t('stop_timer')}" aria-label="${t('stop_timer')}">■</button>
              </span>
            </div>
            <button type="button" class="lockin-btn" id="lockin-exit-btn" title="${t('exit_title')}" aria-label="${t('exit_title')}">✕ <span class="lockin-exit-label">${t('exit')}</span></button>
          </div>
        `;
        document.body.appendChild(bar);
        
        // Bind click handler for exit button
        document.getElementById('lockin-exit-btn').addEventListener('click', () => {
          safePycmd("anki-lockin-exit");
        });
        document.getElementById('lockin-cat-chip').addEventListener('click', () => {
          safePycmd('lockin-open-cat-collection');
        });
        const pauseBtn = document.getElementById('lockin-pomo-pause-btn');
        if (pauseBtn) pauseBtn.addEventListener('click', pausePomodoro);
        const resumeBtn = document.getElementById('lockin-pomo-resume-btn');
        if (resumeBtn) resumeBtn.addEventListener('click', resumePomodoro);
        const stopBtn = document.getElementById('lockin-pomo-stop-btn');
        if (stopBtn) stopBtn.addEventListener('click', stopPomodoro);
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
        breakOverlay.lang = getLocale();
        breakOverlay.setAttribute('role', 'dialog');
        breakOverlay.setAttribute('aria-modal', 'true');
        breakOverlay.setAttribute('aria-labelledby', 'lockin-break-title');
        breakOverlay.innerHTML = `
          <div class="lockin-break-content">
            <h1 class="lockin-break-title" id="lockin-break-title">${t('break_title')}</h1>
            <p class="lockin-break-subtitle">${t('break_subtitle')}</p>
            <div class="lockin-breathing-container">
              <div id="lockin-breathing-bubble">${t('breathe')}</div>
            </div>
            <div id="lockin-break-timer">05:00</div>
            <div class="lockin-break-controls">
              <button type="button" class="lockin-btn" id="lockin-break-pause-btn">${t('pause_timer')}</button>
              <button type="button" class="lockin-btn" id="lockin-break-resume-btn">${t('resume_timer')}</button>
              <button type="button" class="lockin-btn" id="lockin-break-reset-btn">${t('stop_timer')}</button>
            </div>
            <button type="button" class="lockin-btn" id="lockin-skip-break-btn">${t('skip_break')}</button>
          </div>
        `;
        document.body.appendChild(breakOverlay);
        document.getElementById('lockin-skip-break-btn').addEventListener('click', endBreak);
        document.getElementById('lockin-break-pause-btn').addEventListener('click', pausePomodoro);
        document.getElementById('lockin-break-resume-btn').addEventListener('click', resumePomodoro);
        document.getElementById('lockin-break-reset-btn').addEventListener('click', stopPomodoro);
      }
    }

    createUnlockToastRegion();
    detectTheme();
    updateCollectionUI();
    updateUI();
    updateSpacerHeight(!!window.AnkiLockInAnswerMode);
  }

  function updateUI() {
    const healthFill = document.getElementById('lockin-health-fill');
    const healthBadge = document.getElementById('lockin-health-badge');
    const streakBadge = document.getElementById('lockin-streak-badge');
    const pomoTimer = document.getElementById('lockin-pomo-timer');
    const pomoStatus = document.getElementById('lockin-pomo-status');
    const breakTimer = document.getElementById('lockin-break-timer');
    const root = document.documentElement;

    const pomoPauseBtn = document.getElementById('lockin-pomo-pause-btn');
    const pomoResumeBtn = document.getElementById('lockin-pomo-resume-btn');
    const pomoDot = document.querySelector('#lockin-pomo-status .lockin-dot');

    const minutes = Math.floor(pomoTimeLeft / 60);
    const seconds = Math.floor(pomoTimeLeft % 60);
    const timeStr = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

    if (pomoTimer) {
      pomoTimer.textContent = timeStr;
    }
    if (breakTimer) {
      breakTimer.textContent = timeStr;
    }

    if (pomoPauseBtn && pomoResumeBtn) {
      if (pomoState === 'paused') {
        pomoPauseBtn.style.display = 'none';
        pomoResumeBtn.style.display = 'inline-flex';
      } else {
        pomoPauseBtn.style.display = 'inline-flex';
        pomoResumeBtn.style.display = 'none';
      }
    }

    const breakPause = document.getElementById('lockin-break-pause-btn');
    const breakResume = document.getElementById('lockin-break-resume-btn');
    if (breakPause) breakPause.style.display = pomoState === 'paused' ? 'none' : 'inline-flex';
    if (breakResume) breakResume.style.display = pomoState === 'paused' ? 'inline-flex' : 'none';

    if (pomoDot) {
      if (pomoState === 'break' || (pomoState === 'paused' && pomoPhaseBeforePause === 'break')) {
        pomoDot.className = 'lockin-dot pomo-break';
      } else {
        pomoDot.className = 'lockin-dot';
      }
    }

    const isBreakActive = pomoState === 'break' || (pomoState === 'paused' && pomoPhaseBeforePause === 'break');
    const overlay = document.getElementById('lockin-break-overlay');
    if (overlay) {
      if (isBreakActive) {
        overlay.classList.add('overlay-open');
      } else {
        overlay.classList.remove('overlay-open');
      }
    }

    // 1. Health Bar & Warm-Up Grace status
    const cycleActive = Math.max(0, activeStudyDuration - cycleStudyStart);
    const isGracePeriod = (cycleActive < config.grace_period_duration);
    const healthPercent = (health / config.health_max) * 100;
    
    if (healthFill) healthFill.style.width = `${healthPercent}%`;
    
    if (healthBadge) {
      if (isGracePeriod) {
        const warmSecondsLeft = Math.ceil(config.grace_period_duration - cycleActive);
        healthBadge.textContent = t('warmup', { seconds: warmSecondsLeft });
        healthBadge.classList.add('lockin-warmup-badge');
      } else {
        healthBadge.textContent = t('health', { value: Math.round(health) });
        healthBadge.classList.remove('lockin-warmup-badge');
      }
    }
    const healthProgress = document.querySelector('.lockin-health-bar-container[role="progressbar"]');
    if (healthProgress) {
      healthProgress.setAttribute('aria-valuemax', String(config.health_max));
      healthProgress.setAttribute('aria-valuenow', String(Math.round(health)));
      healthProgress.setAttribute('aria-valuetext', t('health', { value: Math.round(health) }));
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
        streakBadge.textContent = t('streak', { value: streak });
        streakBadge.classList.add('streak-visible');
      } else {
        streakBadge.textContent = t('streak', { value: streak });
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
    pomoPhaseBeforePause = 'break';
    pomoState = 'break';
    pomoTimeLeft = config.pomodoro_break_duration * 60;
    breakStartTime = Date.now();
    playSound('break_start');

    // Show Full screen break overlay
    const overlay = document.getElementById('lockin-break-overlay');
    if (overlay) overlay.classList.add('overlay-open');
    if (unlockToastActive && unlockDismissTimer) {
      clearTimeout(unlockDismissTimer);
      unlockDismissTimer = null;
    }

    // Change pomo dot indicator
    const dot = document.querySelector('#lockin-bar .lockin-dot');
    if (dot) {
      dot.className = 'lockin-dot pomo-break';
    }
    syncPomoState();
  }

  function endBreak() {
    pomoPhaseBeforePause = 'study';
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

    // Refresh warm-up grace period for this pomodoro cycle
    cycleStudyStart = activeStudyDuration;
    // Reset idle timer so the break duration doesn't kill the streak
    cardStartTime = Date.now();

    // Auto heal a tiny bit after break to encourage starting
    health = Math.min(config.health_max, health + 15);
    syncPomoState();
    updateUI();
    if (unlockToastActive) scheduleUnlockDismiss();
    renderNextUnlockToast();
  }

  const BREATHING_PATTERNS = {
    'box': {
      name: 'Box Breathing (4-4-4-4)',
      phases: [
        { labelKey: 'breathe_in', duration: 4, action: 'inhale' },
        { labelKey: 'breathe_hold', duration: 4, action: 'hold-full' },
        { labelKey: 'breathe_out', duration: 4, action: 'exhale' },
        { labelKey: 'breathe_hold', duration: 4, action: 'hold-empty' }
      ]
    },
    '478': {
      name: '4-7-8 Relaxation (4-7-8)',
      phases: [
        { labelKey: 'breathe_in', duration: 4, action: 'inhale' },
        { labelKey: 'breathe_hold', duration: 7, action: 'hold-full' },
        { labelKey: 'breathe_out', duration: 8, action: 'exhale' }
      ]
    },
    'coherent': {
      name: 'Coherent Breathing (5-5)',
      phases: [
        { labelKey: 'breathe_in', duration: 5, action: 'inhale' },
        { labelKey: 'breathe_out', duration: 5, action: 'exhale' }
      ]
    },
    'sigh': {
      name: 'Physiological Sigh (4-2-8)',
      phases: [
        { labelKey: 'breathe_double', duration: 4, action: 'inhale' },
        { labelKey: 'breathe_hold', duration: 2, action: 'hold-full' },
        { labelKey: 'breathe_sigh', duration: 8, action: 'exhale' }
      ]
    },
    'simple': {
      name: 'Equal Breathing (4-4)',
      phases: [
        { labelKey: 'breathe_in', duration: 4, action: 'inhale' },
        { labelKey: 'breathe_out', duration: 4, action: 'exhale' }
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

    breathingBubble.textContent = t(activePhase.labelKey || 'breathe');

    if (prefersReducedMotion()) {
      breathingBubble.className = 'breathe-reduced-motion';
      breathingBubble.style.transform = 'scale(1)';
      return;
    }

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

    // The reviewer is the sole clock owner. Deck browser/overview webviews may
    // render and control the persisted timer, but must never run a competing
    // countdown or health loop.
    if (isMainWebview && pomoState === 'study' && focusStateActive) {
      // Increment session active study time
      activeStudyDuration += dt;
      if (reviewPhase === 'question') {
        activeQuestionTime += dt;
      }

      // 1. Manage Focus Shield
      if (shieldTimeLeft > 0) {
        shieldTimeLeft = Math.max(0, shieldTimeLeft - dt);
      }

      // 2. Grace Period & Sigmoid Health depletion. Reading the answer side
      // is study time, but it must not cost health.
      const cycleActive = Math.max(0, activeStudyDuration - cycleStudyStart);
      const isGracePeriod = (cycleActive < config.grace_period_duration);
      if (!isGracePeriod && shieldTimeLeft === 0 && reviewPhase === 'question') {
        const streakMultiplier = getStreakMultiplier();
        const cardTime = activeQuestionTime;

        // Sigmoid drain factor starts slow (10% drain) and accelerates as user stalls
        let drainFactor = 1.0;
        if (config.sigmoid_drain_enabled) {
          drainFactor = 0.1 + 1.9 / (1 + Math.exp(-0.35 * (cardTime - 6)));
        }

        health = Math.max(0, health - (config.health_drain_rate * dt * drainFactor) / streakMultiplier);
      }

      // 3. Heartbeat Audio tick for low health (<25%), only while recalling.
      const healthPercent = (health / config.health_max) * 100;
      if (reviewPhase === 'question' && !isGracePeriod && config.adhd_boosters_low_health_heartbeat && healthPercent < 25 && health > 0) {
        const heartbeatFreq = 300 + (healthPercent / 25) * 1100; // 300ms to 1400ms
        if (now - lastHeartbeatTime >= heartbeatFreq) {
          playSound('heartbeat');
          lastHeartbeatTime = now;
        }
      }

      // 4. Game over is visual/session feedback only. It must never submit an
      // Anki rating because that would corrupt scheduling data.
      if (health === 0) {
        gameOverFreeze = true;
        playSound('gameover');

        const bar = document.getElementById('lockin-bar');
        if (bar) bar.classList.add('lockin-hit');

        if (gameOverTimeout) clearTimeout(gameOverTimeout);
        gameOverTimeout = setTimeout(() => {
          if (bar) bar.classList.remove('lockin-hit');

          health = config.health_max * 0.5; // restore health to 50% grace
          cycleStudyStart = activeStudyDuration;
          streak = 0;
          shieldTimeLeft = 0;
          gameOverFreeze = false;
          gameOverTimeout = null;
          updateUI();
        }, 1500);
      }

      // 5. Question elapsed and speed-focus alerts
      if (reviewPhase === 'question') {
        const questionTime = activeQuestionTime;

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

        // Both timeout settings may reveal the answer, but neither may grade
        // it. onShowAnswer() remains the authoritative phase transition.
        const revealExpired = config.speed_focus_reveal_delay > 0 && questionTime >= config.speed_focus_reveal_delay;
        const legacyFailExpired = config.speed_focus_fail_delay > 0 && questionTime >= config.speed_focus_fail_delay;
        if (!revealRequested && (revealExpired || legacyFailExpired)) {
          questionResponseTime = activeQuestionTime;
          revealRequested = true;
          safePycmd("ans");
        }
      }

      // 6. Study countdown
      pomoTimeLeft = Math.max(0, pomoTimeLeft - dt);
      if (pomoTimeLeft === 0) {
        startBreak();
      }
    }
    else if (isMainWebview && pomoState === 'break' && focusStateActive) {
      pomoTimeLeft = Math.max(0, pomoTimeLeft - dt);

      // Update dynamic breathing exercise
      updateBreathingVisualizer(now);

      if (pomoTimeLeft === 0) {
        endBreak();
      }
    }

    // Sync Pomodoro state to Python every second (1000ms)
    if (isMainWebview && pomoState !== 'paused' && focusStateActive && (now - lastPomoSyncTime >= 1000)) {
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

  // --- Dynamic bottom spacer (prevents card content going under fixed bar/buttons) ---
  // Anki replaces #qa.innerHTML on every card transition, so the spacer must be
  // re-injected in onShowQuestion/onShowAnswer/updateButtons via ensureSpacer().

  function findScrollContainer() {
    const qa = document.getElementById('qa');
    if (!qa) return null;
    // If #qa itself scrolls, use it
    const qaStyle = getComputedStyle(qa);
    if (qaStyle.overflowY === 'auto' || qaStyle.overflowY === 'scroll') {
      return qa;
    }
    // Search for a nested scrollable wrapper (custom templates)
    const descendants = qa.querySelectorAll('*');
    for (let i = 0; i < descendants.length; i++) {
      const el = descendants[i];
      const s = getComputedStyle(el);
      if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
        return el;
      }
    }
    // Fallback: #qa
    return qa;
  }

  function ensureSpacer() {
    if (!isMainWebview) return null;
    const container = findScrollContainer();
    if (!container) return null;
    let spacer = document.getElementById('lockin-spacer');
    if (spacer && spacer.parentElement !== container) {
      spacer.remove();
      spacer = null;
    }
    if (!spacer) {
      spacer = document.createElement('div');
      spacer.id = 'lockin-spacer';
      container.appendChild(spacer);
    }
    return spacer;
  }

  function updateSpacerHeight(isAnswerMode) {
    const spacer = ensureSpacer();
    if (!spacer) return;
    const barAtBottom = config.bar_position !== 'top';
    const customBtnsVisible = config.custom_answer_buttons && !config.hide_answer_buttons;
    let h = 0;
    if (barAtBottom) {
      h = 54; // lockin-bar height
      if (isAnswerMode && customBtnsVisible) {
        h = 140; // bar + custom answer buttons
      }
    } else {
      // top bar: no bottom bar, but custom buttons sit at bottom in answer mode
      if (isAnswerMode && customBtnsVisible) {
        h = 86;
      }
    }
    spacer.style.height = h + 'px';
  }

  function normalizeRating(data, easeKey) {
    const explicit = data && (data.rating || data.semantic_rating || data.semantic);
    if (explicit) {
      const normalized = String(explicit).toLowerCase();
      if (['again', 'hard', 'good', 'easy'].includes(normalized)) return normalized;
    }
    const label = String((data && data.label) || '').trim().toLowerCase();
    const labels = {
      again: 'again', nochmal: 'again', wieder: 'again',
      hard: 'hard', schwer: 'hard',
      good: 'good', gut: 'good',
      easy: 'easy', einfach: 'easy'
    };
    if (labels[label]) return labels[label];
    return ({ '1': 'again', '2': 'hard', '3': 'good', '4': 'easy' })[String(easeKey)] || '';
  }

  function renderQuestionControls() {
    const wrap = document.getElementById('lockin-answer-buttons');
    if (!wrap) return;
    wrap.innerHTML = '';
    const customBtnsVisible = isMainWebview && config.custom_answer_buttons && !config.hide_answer_buttons;
    if (!customBtnsVisible) {
      setAnswerButtonsVisible(false);
      updateSpacerHeight(false);
      return;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'lockin-show-ans-btn';
    btn.className = 'lockin-ans-btn lockin-show-ans-btn';
    btn.setAttribute('aria-keyshortcuts', 'Space');
    btn.setAttribute('aria-label', t('show_answer'));
    btn.title = t('show_answer') + ' (Space)';
    const labelSpan = document.createElement('span');
    labelSpan.className = 'lockin-ans-label';
    labelSpan.textContent = t('show_answer');
    btn.appendChild(labelSpan);

    btn.addEventListener('pointerdown', function () {
      if (prefersReducedMotion()) return;
      btn.classList.add('lockin-ans-picked');
      setTimeout(function () { btn.classList.remove('lockin-ans-picked'); }, 200);
    });
    btn.addEventListener('click', function () {
      safePycmd('ans');
    });
    wrap.appendChild(btn);
    setAnswerButtonsVisible(true);
    updateSpacerHeight(true);
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
      activeQuestionTime = 0;
      reviewPhase = 'question';
      window.AnkiLockInAnswerMode = false;
      questionResponseTime = null;
      revealRequested = false;
      alertSoundPlayed = false;
      renderQuestionControls();
      updateUI();
    },

    onShowAnswer: function () {
      initAudio();
      if (questionResponseTime === null) {
        questionResponseTime = activeQuestionTime;
      }
      reviewPhase = 'answer';
      window.AnkiLockInAnswerMode = true;
      revealRequested = false;
      if (latestButtonsPayload) {
        AnkiLockIn.updateButtons(latestButtonsPayload);
      }
      const customBtnsVisible = isMainWebview && config.custom_answer_buttons && !config.hide_answer_buttons;
      setAnswerButtonsVisible(customBtnsVisible);
      updateSpacerHeight(customBtnsVisible);
    },

    /**
     * Rebuild the custom answer button row from data pushed by Python.
     * buttonsData is an object keyed by numeric ease (string) with values:
     *   { rating: "again" | "hard" | "good" | "easy",
     *     label: "Again" | "Hard" | "Good" | "Easy", interval: "10d" | "" }
     * Buttons are wired to Anki's native pycmd("easeN") so the click goes
     * through _linkHandler -> _answerCard (same path as native buttons).
     */
    updateButtons: function (buttonsData) {
      latestButtonsPayload = buttonsData;
      if (reviewPhase === 'question') {
        renderQuestionControls();
        return;
      }
      const wrap = document.getElementById('lockin-answer-buttons');
      if (!wrap) return;
      if (!buttonsData || typeof buttonsData !== 'object') return;

      // Clear existing buttons
      wrap.innerHTML = '';

      const clsMap = {
        again: 'lockin-ans--again',
        hard: 'lockin-ans--hard',
        good: 'lockin-ans--good',
        easy: 'lockin-ans--easy'
      };

      Object.keys(buttonsData).forEach(function (easeKey) {
        const data = buttonsData[easeKey] || {};
        const rating = normalizeRating(data, easeKey);
        const translatedLabel = rating ? t('answer_' + rating) : (data.localized_label || data.label || easeKey);
        const interval = data.interval ? String(data.interval) : '';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'lockin-ans-btn ' + (clsMap[rating] || '');
        btn.setAttribute('data-ease', easeKey);
        if (rating) btn.setAttribute('data-rating', rating);
        btn.setAttribute('aria-keyshortcuts', easeKey);
        btn.title = interval ? `${translatedLabel} — ${interval}` : translatedLabel;
        btn.setAttribute('aria-label', btn.title);

        const labelSpan = document.createElement('span');
        labelSpan.className = 'lockin-ans-label';
        labelSpan.textContent = translatedLabel;
        btn.appendChild(labelSpan);
        if (interval) {
          const intervalSpan = document.createElement('span');
          intervalSpan.className = 'lockin-ans-interval';
          intervalSpan.setAttribute('data-interval-for', easeKey);
          intervalSpan.textContent = interval;
          btn.appendChild(intervalSpan);
        }

        // Missing interval text is not evidence that a valid rating is
        // unsupported. Only an explicit backend flag may disable it.
        if (data.enabled === false || data.available === false) {
          btn.classList.add('lockin-ans-unsupported');
          btn.disabled = true;
          btn.setAttribute('aria-disabled', 'true');
        }
        // pointerdown gives the pop animation a ~100ms head start during the
        // physical press, before the click event fires pycmd and Anki transitions.
        btn.addEventListener('pointerdown', function () {
          if (prefersReducedMotion()) return;
          btn.classList.add('lockin-ans-picked');
          setTimeout(function () { btn.classList.remove('lockin-ans-picked'); }, 200);
        });
        btn.addEventListener('click', function () {
          if (typeof pycmd === 'function') { pycmd('ease' + easeKey); }
          else if (typeof window.pycmd === 'function') { window.pycmd('ease' + easeKey); }
        });
        wrap.appendChild(btn);
      });

      // Anki's main webview persists across card transitions, so the row
      // is naturally visible from the moment it is populated. If we land
      // here while in answer mode, make sure it isn't stuck hidden.
      if (reviewPhase === 'answer' || window.AnkiLockInAnswerMode) {
        setAnswerButtonsVisible(true);
      }
      updateSpacerHeight(reviewPhase === 'answer');
    },

    updateCollection: function (snapshot) {
      collectionSnapshot = snapshot && typeof snapshot === 'object' ? snapshot : null;
      window.AnkiLockInCollectionSnapshot = collectionSnapshot;
      updateStaticLocalizedUI();
      updateCollectionUI();
      updateUI();
    },

    showCatUnlock: function (events) {
      queueCatUnlocks(events);
    },

    flushStats: function () {
      // Reuse the regular loop's elapsed-time accounting. tick() advances
      // lastTickTime, so the next scheduled tick cannot count this slice twice.
      tick();
      sendStatsToPython();
    },

    onAnswerCard: function (ease, label, undoCounter) {
      initAudio();
      if (gameOverTimeout) {
        clearTimeout(gameOverTimeout);
        gameOverTimeout = null;
        gameOverFreeze = false;
      }

      const preAnswerState = {
        health: health,
        streak: streak,
        statsCards: statsCards,
        statsCorrect: statsCorrect,
        statsMaxStreak: statsMaxStreak,
        shieldTimeLeft: shieldTimeLeft,
        statsShields: statsShields
      };
      const answerSpeed = questionResponseTime !== null ? questionResponseTime : activeQuestionTime;
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

      // Heal/damage bar
      health = Math.max(0, Math.min(config.health_max, health + healthDelta));

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

      const postAnswerState = {
        health: health,
        streak: streak,
        statsCards: statsCards,
        statsCorrect: statsCorrect,
        statsMaxStreak: statsMaxStreak,
        shieldTimeLeft: shieldTimeLeft,
        statsShields: statsShields
      };
      preAnswerState.post = postAnswerState;
      storeAnswerAction(undoCounter, preAnswerState);

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
      const overlay = document.getElementById('lockin-break-overlay');
      if (overlay) overlay.remove();
      const lowHealth = document.getElementById('lockin-low-health-overlay');
      if (lowHealth) lowHealth.remove();
      const ansBtns = document.getElementById('lockin-answer-buttons');
      if (ansBtns) ansBtns.remove();
      const oldSpacer = document.getElementById('lockin-spacer');
      if (oldSpacer) oldSpacer.remove();
      
      document.body.classList.remove('lockin-active', 'lockin-bar-top', 'lockin-bar-bottom', 'lockin-main', 'lockin-bottom-webview', 'lockin-deckbrowser', 'lockin-overview');
      
      createUI();
      if (reviewPhase === 'question') {
        renderQuestionControls();
      } else if (reviewPhase === 'answer' || window.AnkiLockInAnswerMode) {
        if (latestButtonsPayload) {
          AnkiLockIn.updateButtons(latestButtonsPayload);
        }
        const customBtnsVisible = isMainWebview && config.custom_answer_buttons && !config.hide_answer_buttons;
        setAnswerButtonsVisible(customBtnsVisible);
        updateSpacerHeight(customBtnsVisible);
      }
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
          if (!prefersReducedMotion()) {
            btn.classList.add('lockin-ans-picked');
            setTimeout(() => {
              btn.classList.remove('lockin-ans-picked');
            }, 200);
          }
        } else {
          btn.classList.add('lockin-btn-feedback');
          setTimeout(() => {
            btn.classList.remove('lockin-btn-feedback');
          }, 150);
        }
      }
    },

    onUndo: function (undoCounter) {
      const snapshot = takeLatestAnswerAction(undoCounter);
      if (!snapshot) return;

      // The undone card returns to its question phase. Restore the full snapshot
      // rather than subtracting deltas, and re-render the question controls so
      // the custom "Show Answer" button remains available.
      reviewPhase = 'question';
      window.AnkiLockInAnswerMode = false;
      renderQuestionControls();
      health = snapshot.health;
      streak = snapshot.streak;
      statsCards = snapshot.statsCards;
      statsCorrect = snapshot.statsCorrect;
      statsMaxStreak = snapshot.statsMaxStreak;
      shieldTimeLeft = snapshot.shieldTimeLeft;
      statsShields = snapshot.statsShields;
      sendStatsToPython();
      updateUI();
    },

    onRedo: function (undoCounter) {
      const snapshot = takeLatestRedoAction(undoCounter);
      if (!snapshot) return;

      health = snapshot.health;
      streak = snapshot.streak;
      statsCards = snapshot.statsCards;
      statsCorrect = snapshot.statsCorrect;
      statsMaxStreak = snapshot.statsMaxStreak;
      shieldTimeLeft = snapshot.shieldTimeLeft;
      statsShields = snapshot.statsShields;
      sendStatsToPython();
      updateUI();
    },

    createUI: createUI,
    resetSession: function () {
      health = config.health_max;
      streak = 0;
      shieldTimeLeft = 0;
      reviewPhase = window.AnkiLockInAnswerMode ? 'answer' : 'question';
      activeQuestionTime = 0;
      questionResponseTime = null;
      revealRequested = false;
      alertSoundPlayed = false;
      statsCards = 0;
      statsCorrect = 0;
      statsMaxStreak = 0;
      statsShields = 0;
      clearAnswerActions();
      activeStudyDuration = 0;
      cycleStudyStart = 0;
      gameOverFreeze = false;
      if (gameOverTimeout) {
        clearTimeout(gameOverTimeout);
        gameOverTimeout = null;
      }
      
      // Load Pomodoro state from injected global variables if available
      if (window.AnkiLockInPomoState && window.AnkiLockInPomoState.time_left !== -1) {
        pomoState = window.AnkiLockInPomoState.state;
        pomoTimeLeft = window.AnkiLockInPomoState.time_left;
        pomoPhaseBeforePause = window.AnkiLockInPomoState.pre_pause_state === 'break' ? 'break' :
          (pomoState === 'break' ? 'break' : 'study');
      } else {
        pomoState = 'study';
        pomoPhaseBeforePause = 'study';
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
        if (reviewPhase === 'answer') {
          ease = 3; // Treat as default Good ease for visual highlight
        }
      }

      if (ease !== null && reviewPhase === 'answer' && !prefersReducedMotion()) {
        const btn = document.querySelector(`.lockin-ans-btn[data-ease="${ease}"]`);
        if (btn) {
          btn.classList.add('lockin-ans-picked');
          setTimeout(() => btn.classList.remove('lockin-ans-picked'), 200);
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
