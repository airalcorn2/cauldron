# Step 5: Testing the Webcam

1. Plug the webcam into any USB port.
2. Confirm the Pi sees a video device:
   ```bash
   ls /dev/video*
   ```
   You should see `/dev/video0`, possibly alongside extra nodes such as `/dev/video1`.
   UVC webcams commonly register several.
3. Take a test photo with `fswebcam`, installed back in [Step 2](step-2-dependencies-and-api-keys.md):
   ```bash
   fswebcam -r 1280x720 test.jpg
   ```
4. Open `test.jpg` and confirm it captured what the camera was pointed at.
   Copy it off with `scp` if you are working over SSH.
5. After [Step 2](step-2-dependencies-and-api-keys.md), you can also capture through the project's own module:
   ```bash
   .venv/bin/python camera.py test.jpg
   ```
6. Once the camera is mounted at its final distance from the tray, calibrate the focus.
   The camera's own continuous autofocus does not reliably lock in the tray's dim, close-range scene, so `camera.py` pins a fixed lens position instead.
   Put something textured on the tray and sweep for the sharpest value:
   ```bash
   .venv/bin/python camera.py --sweep-focus
   ```
   Set `CAMERA_FOCUS` in `camera.py` to whatever it reports, and re-run this if the camera is ever remounted at a different distance.

[← Previous: Step 4: Testing Text-to-Speech](step-4-text-to-speech.md) · [↑ Back to README](../README.md) · [Next: Step 6: Testing the Vision Model →](step-6-vision-model.md)
