# The Live Object Model, as this bridge reaches it

**391 properties and 172 functions across 21 object types — 563 operations**, every one reachable with four generic tools:

| | |
|---|---|
| `live_get` | read any property below |
| `live_set` | write any writable one |
| `live_call` | call any function below |
| `live_batch` | up to 500 of the above in one round-trip |

You do not need this page to use the bridge — ask in plain language and the assistant picks the tool. It is here so you can see the coverage is the whole object model rather than a curated subset, and to check whether something specific is reachable. The named tools in [TOOLS.md](TOOLS.md) are an ergonomic layer over this, not a limit on it.

### How this was counted

Read from a running **Ableton Live 12.4.3** by walking the object graph with `live_children` — 713 paths — not copied from a manual. Another Live version will differ.

Three things are deliberately **excluded**, because including them would inflate the number without adding capability:

- **771 listener functions.** Every observable property carries `add_X_listener`, `remove_X_listener` and `X_has_listener`. They follow that pattern exactly and `live_observe` / `live_unobserve` / `live_events` already cover them. Counting them would take the total to 1334.
- **Python builtins and containers.** A crawl reaches `str`, `dict`, `int`, `float` and Vector wrappers through properties that hold ordinary values. `str` alone offers 47 methods — Python's, not Live's.
- **Enumerations**, listed at the end. They are constants you pass to `live_set`, not operations.

## Song

*The set itself — transport, tempo, scenes, tracks, undo, cue points, scale.*

**58 properties** · **40 functions**

**Properties** — `live_get` to read, `live_set` to write:

`appointed_device`, `arrangement_overdub`, `back_to_arranger`, `can_capture_midi`, `can_jump_to_next_cue`, `can_jump_to_prev_cue`, `can_redo`, `can_undo`, `canonical_parent`, `clip_trigger_quantization`, `count_in_duration`, `cue_points`, `current_song_time`, `exclusive_arm`, `exclusive_solo`, `file_path`, `groove_amount`, `groove_pool`, `is_ableton_link_enabled`, `is_ableton_link_start_stop_sync_enabled`, `is_counting_in`, `is_playing`, `last_event_time`, `loop`, `loop_length`, `loop_start`, `master_track`, `metronome`, `midi_recording_quantization`, `name`, `nudge_down`, `nudge_up`, `overdub`, `punch_in`, `punch_out`, `re_enable_automation_enabled`, `record_mode`, `return_tracks`, `root_note`, `scale_intervals`, `scale_mode`, `scale_name`, `scenes`, `select_on_launch`, `session_automation_record`, `session_record`, `session_record_status`, `signature_denominator`, `signature_numerator`, `song_length`, `start_time`, `swing_amount`, `tempo`, `tempo_follower_enabled`, `tracks`, `tuning_system`, `view`, `visible_tracks`

**Functions** — `live_call`:

`View`, `begin_undo_step`, `capture_and_insert_scene`, `capture_midi`, `continue_playing`, `create_audio_track`, `create_midi_track`, `create_return_track`, `create_scene`, `delete_return_track`, `delete_scene`, `delete_track`, `duplicate_scene`, `duplicate_track`, `end_undo_step`, `find_device_position`, `force_link_beat_time`, `get_beats_loop_length`, `get_beats_loop_start`, `get_current_beats_song_time`, `get_current_smpte_song_time`, `get_data`, `is_cue_point_selected`, `jump_by`, `jump_to_next_cue`, `jump_to_prev_cue`, `move_device`, `play_selection`, `re_enable_automation`, `redo`, `scrub_by`, `set_data`, `set_or_delete_cue`, `start_playing`, `stop_all_clips`, `stop_playing`, `sync_parameter_changes`, `tap_tempo`, `trigger_session_record`, `undo`

## Clip

*A MIDI or audio clip: notes, warping, loop, envelopes, groove, colour.*

**48 properties** · **40 functions**

**Properties** — `live_get` to read, `live_set` to write:

`automation_envelopes`, `available_warp_modes`, `canonical_parent`, `color`, `color_index`, `end_marker`, `end_time`, `file_path`, `gain`, `gain_display_string`, `groove`, `has_envelopes`, `has_groove`, `is_arrangement_clip`, `is_audio_clip`, `is_midi_clip`, `is_overdubbing`, `is_playing`, `is_recording`, `is_session_clip`, `is_take_lane_clip`, `is_triggered`, `launch_mode`, `launch_quantization`, `legato`, `length`, `loop_end`, `loop_start`, `looping`, `muted`, `name`, `pitch_coarse`, `pitch_fine`, `playing_position`, `position`, `ram_mode`, `sample_length`, `sample_rate`, `signature_denominator`, `signature_numerator`, `start_marker`, `start_time`, `velocity_amount`, `view`, `warp_markers`, `warp_mode`, `warping`, `will_record_on_start`

**Functions** — `live_call`:

`View`, `add_new_notes`, `add_warp_marker`, `apply_note_modifications`, `automation_envelope`, `beat_to_sample_time`, `clear_all_envelopes`, `clear_envelope`, `create_automation_envelope`, `crop`, `deselect_all_notes`, `duplicate_loop`, `duplicate_notes_by_id`, `duplicate_region`, `fire`, `get_all_notes_extended`, `get_notes`, `get_notes_by_id`, `get_notes_extended`, `get_selected_notes`, `get_selected_notes_extended`, `move_playing_pos`, `move_warp_marker`, `note_number_to_name`, `quantize`, `quantize_pitch`, `remove_notes`, `remove_notes_by_id`, `remove_notes_extended`, `remove_warp_marker`, `replace_selected_notes`, `sample_to_beat_time`, `scrub`, `seconds_to_sample_time`, `select_all_notes`, `select_notes_by_id`, `set_fire_button_state`, `set_notes`, `stop`, `stop_scrub`

## Track

*Any track: mixer, arm/mute/solo, devices, clip slots, routing, freezing.*

**57 properties** · **15 functions**

**Properties** — `live_get` to read, `live_set` to write:

`arm`, `arrangement_clips`, `available_input_routing_channels`, `available_input_routing_types`, `available_output_routing_channels`, `available_output_routing_types`, `back_to_arranger`, `can_be_armed`, `can_be_frozen`, `can_show_chains`, `canonical_parent`, `clip_slots`, `color`, `color_index`, `current_input_routing`, `current_input_sub_routing`, `current_monitoring_state`, `current_output_routing`, `current_output_sub_routing`, `devices`, `fired_slot_index`, `fold_state`, `group_track`, `has_audio_input`, `has_audio_output`, `has_midi_input`, `has_midi_output`, `implicit_arm`, `input_meter_left`, `input_meter_level`, `input_meter_right`, `input_routing_channel`, `input_routing_type`, `input_routings`, `input_sub_routings`, `is_foldable`, `is_frozen`, `is_grouped`, `is_part_of_selection`, `is_showing_chains`, `is_visible`, `mixer_device`, `mute`, `muted_via_solo`, `name`, `output_meter_left`, `output_meter_level`, `output_meter_right`, `output_routing_channel`, `output_routing_type`, `output_routings`, `output_sub_routings`, `performance_impact`, `playing_slot_index`, `solo`, `take_lanes`, `view`

**Functions** — `live_call`:

`View`, `create_audio_clip`, `create_midi_clip`, `create_take_lane`, `delete_clip`, `delete_device`, `duplicate_clip_slot`, `duplicate_clip_to_arrangement`, `duplicate_device`, `get_data`, `insert_device`, `jump_in_running_session_clip`, `monitoring_states`, `set_data`, `stop_all_clips`

## View

*What is on screen: selected track, detail view, follow, highlight.*

**29 properties** · **15 functions**

**Properties** — `live_get` to read, `live_set` to write:

`browse_mode`, `canonical_parent`, `detail_clip`, `device_insert_mode`, `draw_mode`, `drum_pads_scroll_position`, `focused_document_view`, `follow_song`, `grid_is_triplet`, `grid_quantization`, `highlighted_clip_slot`, `is_collapsed`, `is_showing_chain_devices`, `mod_mapping_device`, `mod_mapping_parameter`, `sample_end`, `sample_env_fade_in`, `sample_env_fade_out`, `sample_loop_end`, `sample_loop_fade`, `sample_loop_start`, `sample_start`, `selected_chain`, `selected_device`, `selected_drum_pad`, `selected_parameter`, `selected_scene`, `selected_slice`, `selected_track`

**Functions** — `live_call`:

`NavDirection`, `available_main_views`, `focus_view`, `hide_envelope`, `hide_view`, `is_view_visible`, `scroll_view`, `select_device`, `select_envelope_parameter`, `select_instrument`, `show_envelope`, `show_loop`, `show_view`, `toggle_browse`, `zoom_view`

## RackDevice

*A rack: its chains, macros, variations.*

**27 properties** · **12 functions**

**Properties** — `live_get` to read, `live_set` to write:

`can_compare_ab`, `can_have_chains`, `can_have_drum_pads`, `can_show_chains`, `canonical_parent`, `chain_selector`, `chains`, `class_display_name`, `class_name`, `drum_pads`, `has_drum_pads`, `has_macro_mappings`, `is_active`, `is_showing_chains`, `is_using_compare_preset_b`, `latency_in_ms`, `latency_in_samples`, `macros_mapped`, `name`, `parameters`, `return_chains`, `selected_variation_index`, `type`, `variation_count`, `view`, `visible_drum_pads`, `visible_macro_count`

**Functions** — `live_call`:

`View`, `add_macro`, `copy_pad`, `delete_selected_variation`, `insert_chain`, `randomize_macros`, `recall_last_used_variation`, `recall_selected_variation`, `remove_macro`, `save_preset_to_compare_ab_slot`, `store_chosen_bank`, `store_variation`

## SimplerDevice

*Simpler specifically: the sample, its slices, playback mode.*

**28 properties** · **10 functions**

**Properties** — `live_get` to read, `live_set` to write:

`can_compare_ab`, `can_have_chains`, `can_have_drum_pads`, `can_warp_as`, `can_warp_double`, `can_warp_half`, `canonical_parent`, `class_display_name`, `class_name`, `is_active`, `is_using_compare_preset_b`, `latency_in_ms`, `latency_in_samples`, `multi_sample_mode`, `name`, `note_pitch_bend_range`, `pad_slicing`, `parameters`, `pitch_bend_range`, `playback_mode`, `playing_position`, `playing_position_enabled`, `retrigger`, `sample`, `slicing_playback_mode`, `type`, `view`, `voices`

**Functions** — `live_call`:

`View`, `crop`, `guess_playback_length`, `replace_sample`, `reverse`, `save_preset_to_compare_ab_slot`, `store_chosen_bank`, `warp_as`, `warp_double`, `warp_half`

## Application

*Live itself: version, browser, the view, pressing keys.*

**11 properties** · **12 functions**

**Properties** — `live_get` to read, `live_set` to write:

`average_process_usage`, `browser`, `canonical_parent`, `control_surfaces`, `current_dialog_button_count`, `current_dialog_message`, `number_of_push_apps_running`, `open_dialog_count`, `peak_process_usage`, `unavailable_features`, `view`

**Functions** — `live_call`:

`View`, `get_bugfix_version`, `get_build_id`, `get_document`, `get_major_version`, `get_minor_version`, `get_variant`, `get_version_string`, `has_option`, `press_current_dialog_button`, `show_message`, `show_on_the_fly_message`

## Browser

*Live's browser tree, and previewing or loading from it.*

**17 properties** · **4 functions**

**Properties** — `live_get` to read, `live_set` to write:

`audio_effects`, `clips`, `colors`, `current_project`, `drums`, `filter_type`, `hotswap_target`, `instruments`, `legacy_libraries`, `max_for_live`, `midi_effects`, `packs`, `plugins`, `samples`, `sounds`, `user_folders`, `user_library`

**Functions** — `live_call`:

`load_item`, `preview_item`, `relation_to_hotswap_target`, `stop_preview`

## DrumChain

**17 properties** · **3 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `choke_group`, `color`, `color_index`, `devices`, `has_audio_input`, `has_audio_output`, `has_midi_input`, `has_midi_output`, `in_note`, `is_auto_colored`, `mixer_device`, `mute`, `muted_via_solo`, `name`, `out_note`, `solo`

**Functions** — `live_call`:

`delete_device`, `duplicate_device`, `insert_device`

## ClipSlot

*A cell in Session view: fire it, stop it, create or delete its clip.*

**13 properties** · **7 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `clip`, `color`, `color_index`, `controls_other_clips`, `has_clip`, `has_stop_button`, `is_group_slot`, `is_playing`, `is_recording`, `is_triggered`, `playing_status`, `will_record_on_start`

**Functions** — `live_call`:

`create_audio_clip`, `create_clip`, `delete_clip`, `duplicate_clip_to`, `fire`, `set_fire_button_state`, `stop`

## DeviceParameter

*One knob: value, range, quantisation, automation state.*

**14 properties** · **4 functions**

**Properties** — `live_get` to read, `live_set` to write:

`automation_state`, `canonical_parent`, `default_value`, `display_value`, `is_enabled`, `is_quantized`, `max`, `min`, `name`, `original_name`, `short_value_items`, `state`, `value`, `value_items`

**Functions** — `live_call`:

`begin_gesture`, `end_gesture`, `re_enable_automation`, `str_for_value`

## Chain

*One chain inside a rack, with its own devices and mixer.*

**14 properties** · **3 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `color`, `color_index`, `devices`, `has_audio_input`, `has_audio_output`, `has_midi_input`, `has_midi_output`, `is_auto_colored`, `mixer_device`, `mute`, `muted_via_solo`, `name`, `solo`

**Functions** — `live_call`:

`delete_device`, `duplicate_device`, `insert_device`

## Scene

*A Session row: fire it, name it, its own tempo and time signature.*

**12 properties** · **3 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `clip_slots`, `color`, `color_index`, `is_empty`, `is_triggered`, `name`, `tempo`, `tempo_enabled`, `time_signature_denominator`, `time_signature_enabled`, `time_signature_numerator`

**Functions** — `live_call`:

`fire`, `fire_as_selected`, `set_fire_button_state`

## MixerDevice

*A track's mixer strip: volume, pan, sends, crossfader.*

**12 properties** · **2 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `crossfade_assign`, `crossfader`, `cue_volume`, `left_split_stereo`, `panning`, `panning_mode`, `right_split_stereo`, `sends`, `song_tempo`, `track_activator`, `volume`

**Functions** — `live_call`:

`crossfade_assignments`, `panning_modes`

## BrowserItem

*One item in the browser: its name, whether it is loadable.*

**9 properties** · **0 functions**

**Properties** — `live_get` to read, `live_set` to write:

`children`, `is_device`, `is_folder`, `is_loadable`, `is_selected`, `iter_children`, `name`, `source`, `uri`

## DrumPad

*One pad of a Drum Rack, and the chain behind it.*

**6 properties** · **1 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `chains`, `mute`, `name`, `note`, `solo`

**Functions** — `live_call`:

`delete_all_chains`

## Groove

*One groove in the pool.*

**7 properties** · **0 functions**

**Properties** — `live_get` to read, `live_set` to write:

`base`, `canonical_parent`, `name`, `quantization_amount`, `random_amount`, `timing_amount`, `velocity_amount`

## ChainMixerDevice

*The mixer strip of a chain inside a rack.*

**5 properties** · **0 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `chain_activator`, `panning`, `sends`, `volume`

## CuePoint

*A locator in the arrangement.*

**3 properties** · **1 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `name`, `time`

**Functions** — `live_call`:

`jump`

## GroovePool

*The groove pool itself.*

**2 properties** · **0 functions**

**Properties** — `live_get` to read, `live_set` to write:

`canonical_parent`, `grooves`

## RoutingChannel

*One selectable input or output channel.*

**2 properties** · **0 functions**

**Properties** — `live_get` to read, `live_set` to write:

`display_name`, `layout`

## Enumerations

Constant sets, not operations — the values you pass when writing the matching property. Not counted in the total above.

**ClipSlotPlayingState** — `denominator`, `imag`, `name`, `names`, `numerator`, `real`, `recording`, `started`, `stopped`, `values`

**DeviceType** — `audio_effect`, `denominator`, `imag`, `instrument`, `midi_effect`, `name`, `names`, `numerator`, `real`, `undefined`, `values`

**Quantization** — `denominator`, `imag`, `name`, `names`, `numerator`, `q_2_bars`, `q_4_bars`, `q_8_bars`, `q_bar`, `q_eight`, `q_eight_triplet`, `q_half`, `q_half_triplet`, `q_no_q`, `q_quarter`, `q_quarter_triplet`, `q_sixtenth`, `q_sixtenth_triplet`, `q_thirtytwoth`, `real`, `values`

**RecordingQuantization** — `denominator`, `imag`, `name`, `names`, `numerator`, `real`, `rec_q_eight`, `rec_q_eight_eight_triplet`, `rec_q_eight_triplet`, `rec_q_no_q`, `rec_q_quarter`, `rec_q_sixtenth`, `rec_q_sixtenth_sixtenth_triplet`, `rec_q_sixtenth_triplet`, `rec_q_thirtysecond`, `values`

**RoutingChannelLayout** — `denominator`, `imag`, `midi`, `mono`, `name`, `names`, `numerator`, `real`, `stereo`, `values`

**RoutingType** — `attached_object`, `category`, `display_name`
