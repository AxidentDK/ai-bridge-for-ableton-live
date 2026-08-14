autowatch = 1;
inlets = 1;
outlets = 3;

var isRecording = false;
var lastPath = "";
var lastDurationMs = 0;
var pendingDurationMs = 0;   // stash for the deferred start (Task.arguments proved unreliable in M4L [js])
var commandFile = jsarguments.length > 1 ? String(jsarguments[1]) : "agent_audio_tap_command.json";
// Liveness handshake: every report() also writes a status FILE next to the
// command file, so a client can prove the device instance is actually alive
// (a stale instance — observed after Live's "Collect All and Save" on Windows,
// 2026-08-08 — silently ignores both the command file and UDP, and a freshly
// loaded instance records zeros until Max finishes wiring the audio graph).
var statusFile = commandFile.replace(/command(\.json)?$/, "status$1");
if (statusFile === commandFile) {
    statusFile = commandFile + ".status";
}
var statusSeq = 0;
var lastCommandId = "";
var pollTask = null;
var startTask = null;
var stopTask = null;

function loadbang() {
    start_polling();
    report("loaded");
}

function start_polling() {
    if (!pollTask) {
        pollTask = new Task(pollCommandFile, this);
        pollTask.interval = 100;
        pollTask.repeat();
    }
}

function pollCommandFile() {
    var file = new File(commandFile, "read");
    if (!file.isopen) {
        return;
    }

    var raw = file.readstring(65536);
    file.close();
    if (!raw) {
        return;
    }

    var command;
    try {
        command = JSON.parse(raw);
    } catch (err) {
        outlet(2, "error", "invalid_command_file", String(err));
        return;
    }

    var id = command.id || raw;
    if (id === lastCommandId) {
        return;
    }
    lastCommandId = id;
    handleCommand([command.command, command.path, command.duration_ms]);
}

function anything() {
    var atoms = arrayfromargs(messagename, arguments);
    if (messagename === "/agent_audio_tap") {
        handleCommand(atoms.slice(1));
    } else {
        handle(atoms.join(" "));
    }
}

function list() {
    handle(arrayfromargs(arguments).join(" "));
}

function msg_string(value) {
    handle(value);
}

function handle(raw) {
    var command;

    if (raw === "start" || raw === "stop" || raw === "status") {
        handleCommand([raw]);
        return;
    }

    try {
        command = JSON.parse(raw);
    } catch (err) {
        outlet(2, "error", "invalid_json", String(err));
        return;
    }

    handleCommand([command.command, command.path, command.duration_ms]);
}

function handleCommand(parts) {
    var command = parts[0];
    var path = parts[1];
    // Optional capture duration in milliseconds. When present and positive, the
    // recording self-terminates after that many ms (see startRecording).
    var durationMs = normalizeDuration(parts[2]);

    if (!command) {
        outlet(2, "error", "missing_command");
        return;
    }

    if (command === "open") {
        openPath(path);
    } else if (command === "start") {
        if (path) {
            openPath(path);
            scheduleStartRecording(durationMs);
            return;
        }
        startRecording(durationMs);
    } else if (command === "stop") {
        stopRecording();
    } else if (command === "status") {
        report("status");
    } else {
        outlet(2, "error", "unknown_command", command);
    }
}

function normalizeDuration(value) {
    if (value === undefined || value === null || value === "") {
        return 0;
    }
    var ms = parseFloat(value);
    if (!isFinite(ms) || ms <= 0) {
        return 0;
    }
    return ms;
}

function scheduleStartRecording(durationMs) {
    if (!startTask) {
        startTask = new Task(deferredStart, this);
    }
    // sfrecord~ finalizes/closes the file when a "record <ms>" auto-stop fires,
    // and a fresh "open" is required before the next capture. openPath already
    // re-opened above, so carry the duration through to the deferred start.
    // Stash it in a module var rather than Task.arguments: in Live's [js] the
    // Task.arguments did NOT survive the 500ms deferral (the callback saw 0, so
    // startRecording fell through to the continuous outlet(0,1) and the WAV
    // recorded forever — caught by an in-Live record_track_to_wav test 2026-06-13).
    pendingDurationMs = normalizeDuration(durationMs);
    startTask.schedule(500);
}

function deferredStart() {
    startRecording(pendingDurationMs);
}

function openPath(path) {
    if (!path || typeof path !== "string") {
        outlet(2, "error", "missing_path");
        return;
    }
    lastPath = path;
    outlet(0, "open", path, "wave");
    report("open");
}

function startRecording(durationMs) {
    if (!lastPath) {
        outlet(2, "error", "no_output_path");
        return;
    }
    lastDurationMs = normalizeDuration(durationMs);
    isRecording = true;
    // Start recording continuously, then (for a capped capture) schedule an
    // explicit stop after lastDurationMs. We deliberately do NOT use sfrecord~'s
    // "record <ms>" self-terminate: despite the Max docs it does NOT auto-stop in
    // Live 12.4.1 (verified 2026-06-13 — it recorded continuously for 150s+). The
    // explicit 0 reliably stops AND finalizes the WAV header (also verified), and
    // Task.schedule timing is reliable (only Task.arguments wasn't), so a
    // [js]-scheduled stop is the robust cap.
    if (stopTask) { stopTask.cancel(); }
    outlet(0, 1);
    if (lastDurationMs > 0) {
        if (!stopTask) { stopTask = new Task(timedStop, this); }
        stopTask.schedule(lastDurationMs);
    }
    report("start");
}

function timedStop() {
    // The duration cap fired: stop + finalize the file (same path as an explicit
    // stop, which is what actually finalizes the WAV header).
    isRecording = false;
    outlet(0, 0);
    report("stop");
}

function stopRecording() {
    if (stopTask) { stopTask.cancel(); }
    isRecording = false;
    lastDurationMs = 0;
    outlet(0, 0);
    report("stop");
}

function report(eventName) {
    statusSeq += 1;
    var payload = JSON.stringify({
        event: eventName,
        recording: isRecording,
        path: lastPath,
        // duration_ms is the requested cap for a self-terminating "record <ms>"
        // capture, or 0 for a continuous (stop-terminated) one. NOTE: this is the
        // REQUESTED duration, not a measured byte/length count read back from
        // sfrecord~ — the current patch leaves sfrecord~'s status outlet
        // unwired (numoutlets 0), so the JS cannot observe the finalized file.
        // Wiring sfrecord~'s sync outlet back into this [js] is a follow-up if a
        // measured completion signal is needed.
        duration_ms: lastDurationMs,
        // Handshake fields: last_command_id lets a client match a status write to
        // the exact command it sent; seq distinguishes fresh writes even when the
        // id repeats (e.g. loadbang before any command).
        last_command_id: lastCommandId,
        seq: statusSeq
    });
    outlet(1, payload);
    writeStatusFile(payload);
}

function writeStatusFile(payload) {
    // Overwrite-in-place; eof trim drops any longer stale tail so the file is
    // always exactly one JSON object.
    try {
        var file = new File(statusFile, "write");
        if (!file.isopen) {
            outlet(2, "error", "status_file_unwritable", statusFile);
            return;
        }
        file.position = 0;
        file.writestring(payload);
        file.eof = file.position;
        file.close();
    } catch (err) {
        outlet(2, "error", "status_file_write_failed", String(err));
    }
}
