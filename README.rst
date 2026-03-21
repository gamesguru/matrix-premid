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

If you want to run this constantly in the background as a Linux service:

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/user/matrix-premid
      cd matrix-premid

2. Install the script, systemd service, and dependencies globally:

   .. code-block:: bash

      make install

   This will copy the script to ``/usr/local/bin/matrix_premid``, install the python dependencies globally, and place the systemd service in ``/etc/systemd/system/``.

3. Configure your credentials in the service file:

   .. code-block:: bash

      sudo systemctl edit --full matrix-premid.service

   Edit the ``Environment=`` variables with your Matrix credentials (HOMESERVER, USERNAME, ACCESS_TOKEN, DEVICE_ID).

4. Start and enable the background service:

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
