# Using the AI Bridge — a short guide

You talk to Live in plain language; the assistant drives it. There are no commands
to memorise. This guide covers the few things that are **not** obvious — mostly
places where Live needs something from you first.

## Just ask

"Make a four-bar chord progression in F minor", "what key is this clip in?",
"load Wavetable on track 2", "how loud is the bass track?", "find sounds like that
snare". If it can be done through Live's API, it can be asked for.

## Switching views — ask any time

Live always opens in **Session** view. You can say *"switch to Arranger"* at the
start of a session or at any point later, and it switches. Worth knowing because
some work reads much better in one view than the other — arranging in Arranger,
clip-launching in Session.

## Finding sounds by similarity — this one needs a setup step

The assistant can find sounds that *sound like* another sound, using Live 12's own
audio analysis. This is real audio similarity, not filename matching.

**But Live only analyses folders you have added as "Places".**

**Places** is a category in Live's browser sidebar, below *Collections* and
*Library*. It lists the folders Live knows about — Packs, User Library, Current
Project, plus any folder you have added yourself. At the bottom of that list is
**Add Folder…**

If a sample folder is not in that list, Live never analyses it, and the assistant
can never find those sounds. That is the whole rule.

**To add a folder:** browser sidebar → **Places** → **Add Folder…** → choose the
folder. Live then analyses it in the background; large folders take a while.

**To see where you stand,** ask *"what's the status of Live's sound index?"* — it
reports analysed vs. total per Place, so you can see both which folders are missing
and which are still being processed.

Two limits worth knowing:
- Live analyses only about the **first 2 seconds** of each file, so long evolving
  sounds are matched on how they *start*.
- Files must be under 60 seconds to be analysed at all.

**Don't add everything.** Plugin resource folders (Serum's `Documents\Xfer`
wavetables, u-he, Vital) contain thousands of files that are not browsable samples
and will only clutter the index. Add sample packs and your own recordings.

## Describing clips and audio

Ask *"describe the clips in this set"* and you get musical summaries instead of
filenames — key, chords, texture, register, density, e.g. *"F minor, monophonic,
moderate, mid register, 73% leaps"*. Useful for finding an idea you half-remember.

For audio files, *"describe this WAV"* reports measured character — bright/dark,
tonal/noisy, percussive/sustained, stereo width, key and tempo.

**What it will not do:** claim genre, era or mood. Those are perceptual judgements
that measurement cannot support, and the tools say so rather than guessing. Tempo
in particular is reported with a confidence and withheld when the material has no
clear beat.

## Loading devices

*"Load Vital on track 1"* works — instruments, MIDI effects, audio effects, plugins
and Max for Live devices, all by name. Live places them correctly (a MIDI effect
lands before the instrument).

First search of a category walks Live's browser and can take a few seconds. Ask to
*"build the browser index"* once and later searches are instant. Rebuild it after
installing new plugins or packs.

## Recording audio from inside the chain

The **AgentAudioTap** Max for Live device records audio at whatever point you put it
— before a compressor, on one group bus, on the Main out. Drop it in a chain, start
playback, and ask for a capture; the assistant can then measure exactly what is
happening at that point.

⚠️ If the tap stops responding, quit and restart Live. Max's runtime can go stale,
and a freshly loaded device then silently ignores everything. A restart is the only
reliable fix.

## Things that need you, not the assistant

- **Adding folders as Places** (above).
- **Plugin parameters.** The assistant can *see* every parameter a plugin has, even
  the thousands Live hides — but to *change* one it must first be added on the
  device in Live via **Configure**. One click per parameter, once.
- **Restarting Live** after installing or updating the bridge's remote script.
