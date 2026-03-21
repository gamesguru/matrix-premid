Matrix PreMiD
=============

A Python script that sets your Matrix presence and status based on native OS-level media sessions (MPRIS). It monitors what you are listening to or watching via D-Bus and instantly pushes it to your Matrix account.

Requirements
------------

* Linux OS with D-Bus and MPRIS support
* `playerctl` installed (e.g. ``sudo apt install playerctl`` or ``sudo pacman -S playerctl``)
* Python 3.7+
* A Matrix account and homeserver
* The required Python packages (see ``requirements.txt``)

Installation
------------

The easiest way to install and run the project is using the provided Makefile.

1. Clone the repository:

   .. code-block:: bash

      git clone <repository_url>
      cd matrix-premid

2. Configure your environment:

   Copy the sample environment file and edit it with your Matrix credentials:

   .. code-block:: bash

      cp .env.example .env

   Edit the ``.env`` file and fill in:
   * ``HOMESERVER``: Your homeserver URL (e.g., ``https://matrix.org``)
   * ``USERNAME``: Your Matrix user ID (e.g., ``@user:matrix.org``)
   * ``ACCESS_TOKEN``: Your account access token
   * ``DEVICE_ID``: Your device ID

3. Install the script, systemd service, and dependencies globally:

   .. code-block:: bash

      make install

   This will copy the script to ``/usr/local/bin/matrix_premid``, install the dependencies, and configure the systemd service.

4. Start the background service:

   .. code-block:: bash

      sudo systemctl start matrix-premid.service

Running
-------

Execute the script using the Makefile:

.. code-block:: bash

   make run

The script will listen to Linux MPRIS events natively. As long as the script is running, when you play media in a browser or application (like Spotify, VLC, Firefox), your standard Matrix presence and your custom Element status will be instantly updated. When media is stopped or paused, the status will return to Idle and clear the custom text.

Development
-----------

To install development dependencies (like formatters and linters):

.. code-block:: bash

   make install-dev

You can format the code using Black:

.. code-block:: bash

   make format

You can lint the code using Flake8:

.. code-block:: bash

   make lint

To clean up the virtual environment and cache files:

.. code-block:: bash

   make clean
