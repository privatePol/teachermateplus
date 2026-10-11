/* Optional local presentation cues. Never controls gameplay. */
(() => {
  "use strict";
  window.QuiTizzAudio = function (controls, {speaker = true, sessionId = ""} = {}) {
    let context, enabled = false, muted = false, volume = .35, previous = null, closed = false;
    const nodes = new Set(), seen = new Set();
    const channel = sessionId && window.BroadcastChannel ? new window.BroadcastChannel(`qt-audio-${sessionId}`) : null;
    const status = controls?.querySelector("[data-audio-status]");
    const say = (text) => { if (status) status.textContent = text; };
    function stop() { for (const node of nodes) { try { node.stop?.(); node.disconnect?.(); } catch (_) {} } nodes.clear(); }
    function announce() { channel?.postMessage({muted, volume}); }
    function tone(frequency, start, duration, level = .12) {
      const oscillator = context.createOscillator(), gain = context.createGain();
      oscillator.type = "triangle"; oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start); gain.gain.linearRampToValueAtTime(level * volume, start + .02);
      gain.gain.exponentialRampToValueAtTime(.001, start + duration);
      oscillator.connect(gain); gain.connect(context.destination); nodes.add(oscillator); nodes.add(gain);
      oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); nodes.delete(oscillator); nodes.delete(gain); };
      oscillator.start(start); oscillator.stop(start + duration);
    }
    async function unlock() {
      if (!speaker) { say("Enable Sound directly in the projector tab."); return; }
      try {
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (!Audio) throw new Error();
        context ||= new Audio(); await context.resume();
        if (closed) { context.close(); return; }
        enabled = context.state === "running";
        say(enabled ? "Sound enabled: synthetic drum and fanfare. Applause asset not supplied." : "Sound unavailable; game continues silently.");
      } catch (_) { enabled = false; say("Sound unavailable; game continues silently."); }
    }
    controls?.querySelector("[data-audio-enable]")?.addEventListener("click", unlock);
    controls?.querySelector("[data-audio-mute]")?.addEventListener("click", (event) => {
      muted = !muted; stop(); event.currentTarget.textContent = muted ? "Unmute" : "Mute"; announce();
    });
    controls?.querySelector("[data-audio-volume]")?.addEventListener("input", (event) => {
      volume = Math.max(0, Math.min(1, Number(event.target.value) / 100)); stop(); announce();
    });
    if (channel) channel.onmessage = ({data}) => {
      if (typeof data?.muted === "boolean" && Number.isFinite(data.volume)) {
        muted = data.muted; volume = Math.max(0, Math.min(1, data.volume)); stop();
        const button = controls?.querySelector("[data-audio-mute]"); if (button) button.textContent = muted ? "Unmute" : "Mute";
        const slider = controls?.querySelector("[data-audio-volume]"); if (slider) slider.value = volume * 100;
      }
    };
    function update(state) {
      const phase = state.show_phase || state.status;
      const key = `${state.question?.id || state.position}:${phase}:${state.phase_started_at || "legacy"}`;
      const initial = previous === null, changed = previous !== key; previous = key;
      if (state.paused || ["CANCELLED", "PREPARING"].includes(phase) || changed) stop();
      if (!changed || seen.has(key)) return;
      seen.add(key); if (seen.size > 64) seen.delete(seen.values().next().value);
      // Initial/reconnect recovery seeds the identity without replaying a cue.
      if (initial || state.paused || !enabled || muted || !speaker || context?.state !== "running") return;
      const age = Date.parse(state.server_now) - Date.parse(state.phase_started_at);
      if (!Number.isFinite(age) || age < 0 || age > 2000) return;
      const now = context.currentTime;
      if (phase === "SUSPENSE") for (let i = 0; i < 12; i++) tone(65 + i * 3, now + i * .32, .18, .1);
      else if (state.status === "COMPLETED" && state.leaderboard?.length) [261.63, 329.63, 392, 523.25].forEach((note, i) => tone(note, now + i * .25, .6));
      // No applause asset exists. Do not claim or simulate applause playback.
    }
    function suspend() { stop(); previous = null; }
    function clear() { closed = true; stop(); context?.close?.(); channel?.close(); }
    window.addEventListener("pagehide", clear);
    say("Sound off. Enable in projector; optional applause asset not supplied.");
    return {update, suspend, clear};
  };
})();
