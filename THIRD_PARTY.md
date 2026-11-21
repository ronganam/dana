# Third-party files

Dana's own code is under the [MIT license](LICENSE). These files in the repository come from other projects, under their own licenses:

| Files | What | License |
|---|---|---|
| `android/app/src/main/res/font/inter_*.ttf`, `server/static/vendor/fonts/inter-*.woff2` | [Inter](https://github.com/rsms/inter) | SIL Open Font License 1.1 |
| `android/*/src/main/res/font/dm_mono_regular.ttf` | [DM Mono](https://github.com/googlefonts/dm-mono) | SIL Open Font License 1.1 |
| `android/*/src/main/res/font/instrument_serif_*.ttf` | [Instrument Serif](https://github.com/Instrument/instrument-serif) | SIL Open Font License 1.1 |
| `android/watchface/src/main/res/font/wfs_bebasneue_*.ttf` | [Bebas Neue](https://github.com/dharmatype/Bebas-Neue) | SIL Open Font License 1.1 |
| `server/static/vendor/lucide.min.js` | [Lucide](https://github.com/lucide-icons/lucide) icons | ISC |
| `android/app/src/wake/assets/wakeword/melspectrogram.onnx`, `embedding_model.onnx` | [openWakeWord](https://github.com/dscripka/openWakeWord) feature models | Apache License 2.0 |

`android/app/src/wake/assets/wakeword/hey_dana.onnx` is Dana's own "Hey Dana" model, trained with openWakeWord's training pipeline. That pipeline's public training data comes with its own terms. openWakeWord publishes its own pretrained models under CC BY-NC-SA 4.0 for this reason, so treat this model as non-commercial, or train your own.

Libraries pulled in at build time (Python packages in `server/requirements.txt`, Gradle dependencies, npm packages) keep their own licenses and are not copied into the repository.
