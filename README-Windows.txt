quasi_EMG_streamer - portable Windows application

1. Extract the entire ZIP into a folder you can write to, such as Documents.
2. Open quasi_EMG_streamer.exe.

No Python, Git, or separate Python-package installation is required.
Keep the _internal folder and quasi_emg_settings.json beside the executable.
Do not run the executable directly from inside the ZIP.

Record and process:
1. Enable Remote Data Access and monitoring in BrainVision Recorder.
2. On Start, set Recorder host and RDA port (normally 127.0.0.1 and 51244).
3. Press Start recording before the video's S3 marker. All incoming channels
   and markers are saved without filtering or artifact correction.
4. Press Stop recording after the video finishes. Wait for the saved-file status.
5. Select Video1R/Video2R/Video3R or Video1LR/Video2LR/Video3LR.
   Check the EMG channel pairs and MRI correction option,
   then press Process file. Video selection resets for each new recording.
   R scores 40 right-hand repeats; LR scores 20 repeats per enabled hand.
   Timing comes from block CSVs; each 4-second repeat is scored separately.
6. Review EMG Analysis, then use Save figures + CSV to export the result.

Raw recordings are saved automatically in data beside the executable, with
unique timestamped names. Keep the .vhdr, .eeg and .vmrk files together.
No filename or participant fields are required. Start and Stop sit side by side
above Process file. Stopping this app's capture does not stop Recorder's own
recording. Closing during capture waits for the local files to finish saving.
Interrupted streams retain partial files but cannot be processed from Start.

Online monitoring:
Open Online Monitoring and enter the BrainVision Recorder computer's address
and EMG channel names. Recorder must have Remote Data Access enabled and be
monitoring. Default port: 51244. Default RIGHT channels: F1, F2.
The first signal appears after approximately 30 seconds. The display updates
every 2.5 seconds with approximately 2.5-5 seconds of delay plus computation.
Press Esc or select another tab to leave full screen.

Saved figures and tables appear in the output folder beside the executable.
Advanced processing settings are in quasi_emg_settings.json.
See docs/online_monitoring.md for the complete monitoring guide.

To update, close the application and extract the new ZIP into a new folder.
Keep your old data and output folders and any customized quasi_emg_settings.json.
Window and connection preferences are retained in your Windows user profile.
