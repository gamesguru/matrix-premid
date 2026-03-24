Matrix PreMiD
=============

A Python script that sets your Matrix presence and status based on native OS-level media sessions (MPRIS). It monitors what you are listening to or watching via D-Bus and instantly pushes it to your Matrix account.

Requirements
------------

* Linux OS with D-Bus and MPRIS support
* ``playerctl`` installed (e.g. ``sudo apt install playerctl`` or ``sudo pacman -S playerctl``)
* Python 3.7+
* A Matrix account and homeserver

Global Installation (Systemd Service)
-------------------------------------

If you want to run this constantly in the background as a Linux service, independent of your cloned repository folder:

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/user/matrix-premid
      cd matrix-premid

2. Configure your credentials locally (or edit later):

   .. code-block:: bash

      cp .env.example .env
      nano .env

   *(Note: If you populate the ``.env`` file locally before installing, the installer will automatically copy and use it for the background service.)*

3. Install the script, systemd service, and dependencies globally to ``/opt``:

   .. code-block:: bash

      make install

   This creates the directory ``/opt/matrix-premid``, copies the script and ``.env`` there, sets up an isolated Python virtual environment exclusively for the service, and symlinks the script to ``/usr/local/bin/matrix_premid``. The systemd service is placed in ``/etc/systemd/system/``.

4. (Optional) Edit credentials after installation:

   .. code-block:: bash

      sudo nano /opt/matrix-premid/.env
      sudo systemctl restart matrix-premid.service

5. Start and enable the background service:

   .. code-block:: bash

      sudo systemctl daemon-reload
      sudo systemctl enable --now matrix-premid.service

Development / Local Running
---------------------------

If you want to run the script locally from the folder (for testing or development) without installing it system-wide:

1. Clone the repository and configure your environment:

   .. code-block:: bash

      git clone https://github.com/user/matrix-premid
      cd matrix-premid
      cp .env.example .env

   Edit the ``.env`` file and fill in your Matrix credentials. Make sure to export them to your shell (e.g., using ``direnv allow`` or sourcing the file) because the script reads directly from ``os.environ``.

2. Install development dependencies:

   .. code-block:: bash

      make deps

3. Run the script directly:

   .. code-block:: bash

      make run

The script will listen to Linux MPRIS events natively. As long as the script is running, when you play media in a browser or application (like Spotify, VLC, Firefox), your standard Matrix presence and your custom Element status will be instantly updated. When media is stopped or paused, the status will return to Idle and clear the custom text.

Code Quality Tools
------------------

You can format the code using Black:

.. code-block:: bash

   make format

You can lint the code using Flake8:

.. code-block:: bash

   make lint

To clean up the virtual environment and cache files:

.. code-block:: bash

   make clean
