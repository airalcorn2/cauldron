# Step 6: Testing the Vision Model

Run the generator against the `test.jpg` from [Step 5](step-5-webcam.md).
For a more interesting subject, point the camera at an object and recapture first with `fswebcam -r 1280x720 test.jpg`.

```bash
.venv/bin/python spell_generator.py test.jpg
```

It should print a JSON object with three spell lines, one each for violet, amber, and hazel, referencing whatever is in the photo.

[← Previous: Step 5: Testing the Webcam](step-5-webcam.md) · [↑ Back to README](../README.md) · [Next: Step 7: Testing the IR Break-Beam Sensor →](step-7-ir-sensor.md)
