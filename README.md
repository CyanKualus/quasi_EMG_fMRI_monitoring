# quasi_EMG_streamer

Standalone EMG copy of NeuroCasting. It **records a raw RDA stream and then
processes the saved recording**
and retains the existing mean-envelope plots, high-EMG trial screening, trial
browser, figures and CSV output. The EEG processing package, component fitting,
time-frequency analysis and EEG score tabs are absent. A separate **Online
Monitoring** tab receives BrainAmp MR data through BrainVision Recorder RDA
and displays the TKEO envelope of AAS-corrected EMG in the El Artem plot style.

## Portable Windows application

Extract **`quasi_EMG_streamer-Windows-x64.zip`** into a writable folder, then
open **`quasi_EMG_streamer.exe`**. Python, Git and package installation are not
required. Keep the `_internal` folder and `quasi_emg_settings.json` beside the
executable. Raw recordings go into `data` and saved results into `output` beside it.

The portable build still connects to BrainVision Recorder's RDA server for
live acquisition. See `README-Windows.txt` inside the download for usage and
update instructions.

## Online BrainAmp MR monitoring

Open **Online Monitoring**, enter the Recorder computer's IP address (or keep
`127.0.0.1` on the same computer), leave port **51244**, set the hand channel
pairs, and press **Connect**. In BrainVision Recorder, enable **Remote Data
Access** and start monitoring. The default right-hand pair is **F1, F2**;
the left hand is disabled until its two channel names are entered.

The default pipeline uses **AAS only, with no cardiac processing or ECG
requirement**: 20 seconds of past template history, a 10-second filter buffer,
first corrected display after approximately **30 seconds**, then an update
every **2.5 seconds** with **2.5–5 seconds of delay plus computation**.
Both graphs display TKEO. Each has independent time-window, integer Ymin/Ymax,
signed magnitude N (`10^N`), threshold and freeze controls on its left. Scaling
is manual and updates immediately, including while frozen. Stream options fold
away to the right; Connect, Disconnect and the progress bar stay below the
bottom graph's options on the left.

Selecting Online Monitoring opens the window full screen. Press Esc or switch
tabs to restore the previous window size.

See [the connection and monitoring guide](docs/online_monitoring.md) for
Recorder setup, timing assumptions and stream interruption behavior.

## Record and process a stream

With Python 3.12 installed, run **`setup_environment.bat`** once to create the
local environment and install dependencies. Then double-click
**`launch_quasi_EMG_processing.bat`** to open the application.

1. Enable **Remote Data Access** and start monitoring in BrainVision Recorder.
   On this app's **Start** tab, set **Recorder host** and **RDA port** (normally
   `127.0.0.1` and `51244`). These settings are shared with Online Monitoring.
2. Start recording **synchronously with the video**. The app saves
   every incoming channel, at the stream's native sampling rate, with its
   original calibration and markers. No filtering or artifact correction is
   applied during capture. Recorder can continue saving its own larger file.
3. After the video and its scheduled events finish, press **Stop recording**.
   Start and Stop are side by side, above **Process file**. The app flushes and
   closes the recording before enabling video selection.
4. Manually select **Video1R, Video2R, Video3R, Video1LR, Video2LR or Video3LR**. This selection resets for each recording;
   it is never inferred from a filename. The six block schedules and their companion block-level rest/fixation tables
   are bundled in `schedules/`. R videos use only the configured right-hand pair;
   LR videos use each enabled hand. Left-hand settings are retained when choosing R.
5. Set **Remove MRI artifacts using MNE (AAS + cardiac OBS)** as appropriate:

   | Checkbox | Processing |
   |---|---|
   | Clear (default) | Existing bipolar EMG pipeline, with no added artifact correction; use for already corrected BrainVision files. |
   | Ticked | Full MNE AAS + cardiac OBS from the reviewed report, followed by the same EMG pipeline; use for uncorrected BrainVision files. An ECG channel is required. |

6. Press **Process file**. It becomes available only after the file is finalized
   and a video is selected. Review **EMG Analysis**, open the hand's trial
   browser, then use **Save figures + CSV** to save the displayed result.

Each capture gets a unique timestamped name in `data/`, with a `.vhdr` header,
`.eeg` samples and `.vmrk` markers. Keep all three together for later recovery
or reprocessing. No filename or participant entry is needed. Starting another
capture clears the previous on-screen result but preserves its raw files.

Recording, online monitoring, analysis and figure export run separately.
Stopping or closing this app finalizes its local recording; it does not stop
BrainVision Recorder or its larger recording. A Recorder stream Stop also
finalizes the capture. Connection failures, missing blocks, malformed samples,
or a stream restart retain the received prefix but block processing that
incomplete capture. Start a new recording after resolving the error.

## Timing and correction

BrainVision treats the **first recorded sample as video time zero** and uses
the selected video's schedule. No EEG markers are required; any recorded
S3/S4 markers are preserved but do not shift or validate the video timing.
This assumes acquisition and video playback start synchronously; the app does
not automatically detect or compensate for a start delay.
The current R/LR stimuli use a 2-second hand cue followed by four 4-second
repeats in each 18-second block. Each repeat is scored separately, including
its 2-second preparation and 2-second action period. R videos contain 40 right-hand
trials; LR videos contain 20 per hand. Onsets and hand order come from `*_blocks.csv`;
rest durations (10/14/16/18 seconds) and fixation timing come from `*_conn_blocks.csv`.
The old videos and microrepeat CSVs are not used by the app. The source tables and
their hashes are recorded in `processing.json`.

Schedule times are relative to recording start; there is no XDF marker-time shift. The recording
must contain the scheduled events through their ends. XDF retains the original
embedded-trial marker and automatic clock-lag handling.

The offline MNE correction pipeline:

- Correct each EMG monopole and ECG at the native sample rate. Use a stable
  **2.5 s** computational cycle grid starting at sample zero, four preceding
  and four following cycles, excluding the target. Use eight real neighbors
  on one side for edge cycles. At least nine complete cycles are required.
- Resample to **250 Hz**, apply **0.5–100 Hz** zero-phase FIR filtering and a
  **50 Hz** notch of 1 Hz width to EMG.
- Detect ECG R peaks afresh from this file using **10–25 Hz**, automatic
  threshold and duplicate suppression within 120 ms. No prior EEG run or
  saved heartbeat list is required.
- Apply independent monopolar PCA-OBS fits with **four components** at
  **R + 212 ms**, then form the requested bipolar difference.
- Apply the original EMG pipeline: causal 50 Hz notch, causal 20–95 Hz
  Butterworth band-pass, TKEO envelope, 100 ms window / 20 ms step and the
  original rest/preparation-based trial classifier.

MRI correction uses pinned MNE **1.13.0.dev334+g64473254e**, commit
`64473254ed0c2c64627a5864a666686a43ef8be8`, and the scientific package versions
listed in `requirements.txt`. Other MNE versions are rejected for correction. Advanced
parameters, including ECG channel and scanner TR, live in
`quasi_emg_settings.json`. GUI preferences use a separate application profile.

Correction can introduce inverse burst copies at neighboring scanner cycles
and alter EMG through cardiac correction. This remains an experimental
correction, and the resulting bandwidth is limited to 100 Hz. The periodic grid
assumes a stable scanner cycle; it does not recover physical scanner triggers.
An incomplete final cycle is not corrected. The incomplete tail and first/last
five seconds are excluded from envelope-based scoring, including overlapping
envelope windows. These exclusions are recorded in `processing.json`.

## Outputs and recording-stop integration

Saved outputs go into this application's folder:

```text
output/<participant>/<filename>/direct/
output/<participant>/<filename>/mne_aas_obs/
```

Each contains `emg_summary.csv`, mean figures, requested trial figures and
metrics, and `processing.json` with parameters, source file identity, timing,
correction details, ECG detections and exclusions. Different files and modes
have separate folders. Repeating the same file and mode replaces its previous
saved output. Source recordings are opened for reading only. Results marked
**UNRELIABLE** retain the original rest false-positive qualification.

The CLI processes and saves a single file immediately, so a recorder can call
it after closing its output files:

```powershell
& .\.venv\Scripts\python.exe run_emg_cli.py "D:\recordings\run.vhdr" --video Video3LR
& .\.venv\Scripts\python.exe run_emg_cli.py "D:\recordings\raw.vhdr" --video Video3LR --mne-mri
```

Add `--wait-for-stop` if the file may still be open. Add `--no-trial-figures`
to save a mean plot and summary without the per-trial output folder.
Run `--help` for the electrode, ECG, TR, participant and output-folder options.
For recovery, the GUI can also process a supplied file through launch arguments
(select a video explicitly for BrainVision):

```powershell
& .\.venv\Scripts\python.exe app.py "D:\recordings\raw.vhdr" --video Video3LR --mne-mri --process
```

Original EMG algorithm and interface copied from NeuroCasting_QuasifMRI.

## Building the portable application

On Windows x64 with Python 3.12 installed, run **`build_standalone.bat`**.
It creates an isolated `.build-venv`, installs the pinned scientific packages
and PyInstaller, then produces `dist/quasi_EMG_streamer-Windows-x64.zip`.
Only the builder needs Python; users receive the complete ZIP.
