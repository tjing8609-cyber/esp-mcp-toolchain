# MicroPython Example

`regression/manifest.json` is a reviewed local selection catalog for the
MicroPython regression examples. It is not parsed automatically by
`esp_regression_test`, does not prove that any file is already on the board,
and does not authorize execution.

Use the `safe` profile by default. Before a run:

1. Read the selected local scripts.
2. Upload each script to its exact `remote_path`.
3. Show the final remote path list and side effects to the user.
4. Call `esp_regression_test` only after explicit execution confirmation.

`hardware_readonly`, `stateful`, and `negative_contract` require explicit
selection. The negative case must run separately because its expected tool
result is a controlled failure. The curated suite excludes GPIO25, the buzzer,
and PWM.
