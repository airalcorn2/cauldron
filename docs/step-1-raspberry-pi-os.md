# Step 1: Setting Up Raspberry Pi OS

1. On another computer, download and install the **Raspberry Pi Imager** from raspberrypi.com/software.
2. Insert the microSD card, using a USB adapter if needed.
3. In the Imager, choose "Raspberry Pi OS (64-bit)" and select the SD card as the target.
4. Set a hostname, username, and password, and enable SSH so you can work headless.
5. Enable Raspberry Pi Connect for browser-based remote access.
6. Write the image, insert the card into the Pi, and connect power.
7. Wait about a minute for it to boot, then SSH in from another computer:
   ```bash
   ssh <username>@<hostname>.local
   ```
   Every step from here on assumes a terminal open on the Pi this way.

[↑ Back to README](../README.md) · [Next: Step 2: Installing Dependencies and API Keys →](step-2-dependencies-and-api-keys.md)
