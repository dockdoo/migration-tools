Hotel Migration
===============

This module is for providing a migration of the hotel content from Odoo 10.0 to odoo 11.0.

**Known Issues**
  - Because models use the same cursor and the Environment holds various caches, these caches
    must be invalidated when altering the database in raw SQL, or further uses of models may become incoherent.

   - Temporal Solution: Uninstall `hotel_calendar` module before migrating.

**External dependencies**
  - OdooRPC, a Python package providing an easy way to pilot your Odoo servers through RPC
