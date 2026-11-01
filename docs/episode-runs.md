# Episode run measurements

This is a sanitized snapshot of the local attempt records, checked on 2026-11-01. It includes every attempt, including failed local processing and the failed generation. It is not a current pricing guide.

There are **14 attempts** across twelve vehicles. The sum of returned `credits_consumed` values is **1430 credits**. Thirteen API tasks returned success at 110 credits each; thirteen attempts have a normalized model, including the recovered historical test. Twelve models were selected for the fleet. The failed ambulance task returned no consumption value; its batch wallet review recorded no net debit for that failure.

| Vehicle | Attempt | API status | Normalized model | Credits returned | Wall seconds | GLB triangles |
|---|---|---|---|---:|---:|---:|
| taxi_city | attempt-01 | success | yes | 110.0 | 168.3 | 46937 |
| taxi_executive | attempt-01 | success | yes | 110.0 | 168.3 | 44310 |
| courier_sprint | attempt-01 | success | yes | 110.0 | 162.5 | 36374 |
| courier_sprint | attempt-02 | success | yes | 110.0 | 236.2 | 48065 |
| courier_parcel | attempt-01 | success | yes | 110.0 | 159.5 | 45533 |
| cargo_freighter | attempt-01 | success | yes | 110.0 | 155.8 | 49105 |
| cargo_utility | attempt-01 | success | yes | 110.0 | 183.5 | 48711 |
| limosine | attempt-01 | success | yes | 110.0 | 137.1 | 48655 |
| sportscar | attempt-01 | success | yes | 110.0 | 135.2 | 44008 |
| rescue_ambulance | attempt-01 | failed | no | not returned | 58.8 |  |
| rescue_ambulance | attempt-02 | success | yes | 110.0 | 198.2 | 45900 |
| municipal_sweeper | attempt-01 | success | yes | 110.0 | 159.5 | 51372 |
| passenger_shuttle | attempt-01 | success | yes | 110.0 | 180.8 | 52448 |
| patrol_interceptor | attempt-01 | success | yes | 110.0 | 146.2 | 48952 |

## What went wrong

- **Courier sprint, attempt 01:** the API succeeded and returned a 110-credit charge, but local GLB loading initially failed because the download was FBX. Local FBX conversion recovered the model without another API charge; the ledger records `local_fbx_recovery`. This historical test requested 20,000 faces. Attempt 02, with the later 25,000-face settings, supplied the selected result.
- **Ambulance, attempt 01:** generation failed with an invalid-input error. A subsequent attempt with fresh uploads succeeded. The four-vehicle expansion consumed 440 credits in total according to its wallet review and successful task records.
- Native output polygon counts can exceed the requested 25,000 budget. Exporting mixed quad/triangle meshes to GLB produces a separate triangle count. Both measurements are retained in the JSON where available.

## Reading the numbers

Wall time is per attempt, including uploads and local work recorded by the runner. Attempts overlap in a parallel batch, so summing these times does not measure batch duration. The earlier eight-vehicle scope has nine charged attempts (990 credits); the later four-vehicle expansion adds 440 credits.

The [JSON snapshot](episode-runs.json) contains only selected measurements, categorical issue notes and state-file hashes. It omits account identifiers, task IDs, uploaded-file tokens, request payloads and signed output URLs. Original state and ledger files stay local.

Historical attempt dimensions are preserved. The later sweeper correction from 5.2 m to 7.2 m is reflected in the current fleet configuration and the shared-scale result image, not retroactively substituted into its attempt record.
