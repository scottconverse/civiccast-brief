// Episode player: speed steps, resume within the browser tab, and chapter seeks.
// Behaviour follows the Craft kit's app/src/client/meeting.js (rewritten here, not copied).
// Resume state lives in sessionStorage for this tab only. The volume a listener picks is kept in
// localStorage so the next episode starts at it. Nothing is sent to a server.
(function () {
  "use strict";

  var SPEEDS = [1, 1.2, 1.5, 1.8, 2];
  var DEFAULT_SPEED = 1.2;
  var STORE_KEY = "civiccast-brief:player";
  var VOLUME_KEY = "civiccast-brief:volume";
  var PLAY_PATH = '<path d="M8 5v14l11-7z"/>';
  var PAUSE_PATH = '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>';

  function readSaved() {
    try {
      return JSON.parse(window.sessionStorage.getItem(STORE_KEY) || "null");
    } catch (error) {
      return null;
    }
  }

  function writeSaved(state) {
    try {
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (error) {
      // Storage can be blocked. The player still works without resume.
    }
  }

  function readVolume() {
    try {
      var value = Number(window.localStorage.getItem(VOLUME_KEY));
      return window.localStorage.getItem(VOLUME_KEY) !== null && value >= 0 && value <= 1 ? value : 1;
    } catch (error) {
      return 1;
    }
  }

  function writeVolume(value) {
    try {
      window.localStorage.setItem(VOLUME_KEY, String(value));
    } catch (error) {
      // Storage can be blocked. The slider still works for this page.
    }
  }

  function clock(seconds) {
    var total = Math.floor(seconds || 0);
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    var pad = secs < 10 ? "0" + secs : String(secs);
    if (hours) {
      return hours + ":" + (minutes < 10 ? "0" + minutes : minutes) + ":" + pad;
    }
    return minutes + ":" + pad;
  }

  function setup(box) {
    var audio = box.querySelector(".player__audio");
    var custom = box.querySelector(".player__custom");
    if (!audio || !custom) {
      return null;
    }
    var playButton = custom.querySelector("[data-play]");
    var speedButton = custom.querySelector("[data-speed]");
    var bar = custom.querySelector("[data-bar]");
    var fill = custom.querySelector("[data-fill]");
    var nowText = custom.querySelector("[data-now]");
    var totalText = custom.querySelector("[data-total]");
    var message = custom.querySelector("[data-msg]");
    var muteButton = custom.querySelector("[data-mute]");
    var volumeSlider = custom.querySelector("[data-volume]");
    var speed = DEFAULT_SPEED;

    // Replace the native controls with the custom ones. The native element stays the audio source.
    audio.removeAttribute("controls");
    custom.hidden = false;
    audio.preload = "metadata";

    function showPlaying(playing) {
      var icon = playButton.querySelector("svg");
      if (icon) {
        icon.innerHTML = playing ? PAUSE_PATH : PLAY_PATH;
      }
      var label = playing ? "Pause" : "Play";
      playButton.setAttribute("aria-label", label);
      playButton.title = label;
    }

    function setSpeed(next) {
      speed = next;
      audio.playbackRate = speed;
      speedButton.textContent = speed + "x";
      speedButton.setAttribute("aria-label", "Playback speed " + speed + "x. Change speed");
    }

    var MUTE_PATH = '<path d="M3 9v6h4l5 5V4L7 9H3zm13.6 3 2.7-2.7-1.4-1.4-2.7 2.7-2.7-2.7-1.4 1.4 2.7 2.7-2.7 2.7 1.4 1.4 2.7-2.7 2.7 2.7 1.4-1.4z"/>';
    var SOUND_PATH = '<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/>';
    var lastVolume = 1;

    function showVolume() {
      var silent = audio.muted || audio.volume === 0;
      var percent = silent ? 0 : Math.round(audio.volume * 100);
      if (volumeSlider) {
        volumeSlider.value = String(percent);
        volumeSlider.setAttribute("aria-valuetext", silent ? "Muted" : "Volume " + percent + "%");
      }
      if (muteButton) {
        var icon = muteButton.querySelector("svg");
        if (icon) {
          icon.innerHTML = silent ? MUTE_PATH : SOUND_PATH;
        }
        var label = silent ? "Unmute" : "Mute";
        muteButton.setAttribute("aria-label", label);
        muteButton.title = label;
      }
    }

    function setVolume(value) {
      audio.volume = Math.min(1, Math.max(0, value));
      audio.muted = audio.volume === 0;
      if (audio.volume > 0) {
        lastVolume = audio.volume;
      }
      writeVolume(audio.volume);
      showVolume();
    }

    function save() {
      writeSaved({ src: audio.currentSrc || audio.src, time: audio.currentTime || 0, speed: speed });
    }

    function start() {
      message.textContent = "";
      var attempt = audio.play();
      if (attempt && typeof attempt.catch === "function") {
        attempt.catch(function () {
          showPlaying(false);
          message.textContent = "The recording could not be played.";
        });
      }
    }

    function seek(seconds) {
      audio.currentTime = Math.max(0, seconds);
      if (audio.paused) {
        start();
      }
      save();
    }

    audio.addEventListener("loadedmetadata", function () {
      totalText.textContent = clock(audio.duration);
      bar.setAttribute("aria-valuetext", clock(audio.currentTime) + " of " + clock(audio.duration));
    });
    audio.addEventListener("timeupdate", function () {
      if (!audio.duration) {
        return;
      }
      var percent = (audio.currentTime / audio.duration) * 100;
      fill.style.width = percent.toFixed(2) + "%";
      nowText.textContent = clock(audio.currentTime);
      bar.setAttribute("aria-valuenow", String(Math.round(percent)));
      bar.setAttribute("aria-valuetext", clock(audio.currentTime) + " of " + clock(audio.duration));
    });
    audio.addEventListener("play", function () { showPlaying(true); });
    audio.addEventListener("pause", function () { showPlaying(false); save(); });
    audio.addEventListener("ended", function () { showPlaying(false); save(); });
    audio.addEventListener("error", function () {
      showPlaying(false);
      message.textContent = "The recording could not be loaded.";
    });

    playButton.addEventListener("click", function () {
      if (audio.paused) {
        start();
      } else {
        audio.pause();
      }
    });

    if (volumeSlider) {
      volumeSlider.addEventListener("input", function () {
        setVolume(Number(volumeSlider.value) / 100);
      });
    }
    if (muteButton) {
      muteButton.addEventListener("click", function () {
        if (audio.muted || audio.volume === 0) {
          setVolume(lastVolume || 1);
        } else {
          audio.muted = true;
          showVolume();
        }
      });
    }
    audio.addEventListener("volumechange", showVolume);

    speedButton.addEventListener("click", function () {
      var index = SPEEDS.indexOf(speed);
      setSpeed(SPEEDS[(index + 1) % SPEEDS.length]);
      save();
    });

    bar.addEventListener("click", function (event) {
      if (!audio.duration) {
        return;
      }
      var rect = bar.getBoundingClientRect();
      seek(((event.clientX - rect.left) / rect.width) * audio.duration);
    });

    bar.addEventListener("keydown", function (event) {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        seek(audio.currentTime + 10);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seek(audio.currentTime - 10);
      }
    });

    window.addEventListener("pagehide", save);

    // Pick up where the listener left off earlier in this tab. Playback does not start by itself.
    var saved = readSaved();
    if (saved && saved.src === (audio.currentSrc || audio.src)) {
      if (SPEEDS.indexOf(saved.speed) !== -1) {
        setSpeed(saved.speed);
      }
      if (saved.time > 0) {
        var resume = function () { audio.currentTime = saved.time; };
        if (audio.readyState >= 1) {
          resume();
        } else {
          audio.addEventListener("loadedmetadata", resume, { once: true });
        }
      }
    }
    setSpeed(speed);
    // Some phones (iOS) fix the volume to the device buttons; there the slider would do nothing.
    var probe = audio.volume;
    audio.volume = 0.5;
    var volumeWorks = audio.volume === 0.5;
    audio.volume = probe;
    var volumeRow = custom.querySelector(".volume-row");
    if (!volumeWorks && volumeRow) {
      volumeRow.hidden = true;
    }
    var startVolume = readVolume();
    audio.volume = startVolume;
    audio.muted = startVolume === 0;
    if (startVolume > 0) {
      lastVolume = startVolume;
    }
    showVolume();
    return { seek: seek };
  }

  function init() {
    var players = [];
    document.querySelectorAll("[data-player]").forEach(function (box) {
      var player = setup(box);
      if (player) {
        players.push(player);
      }
    });
    if (players.length === 0) {
      return;
    }
    var player = players[0];
    document.querySelectorAll(".chapters a[data-start]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        player.seek(Number(link.getAttribute("data-start")) || 0);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
