Matrix PreMiD
=============

A Python script that sets your Matrix presence and status based on a local web server receiving updates. This is particularly useful for integrating with external presence monitors like PreMiD.

Requirements
------------

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

3. Install the dependencies (this will automatically create a virtual environment):

   .. code-block:: bash

      make install

Running
-------

Execute the script using the Makefile:

.. code-block:: bash

   make run

The script will start a local web server on port ``8080``. It will listen for POST requests at ``http://localhost:8080/update`` with a JSON payload like:

.. code-block:: json

   {
       "activity": "Listening to Spotify"
   }

When an update is received, the script updates your standard Matrix presence and your custom Element status.

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
