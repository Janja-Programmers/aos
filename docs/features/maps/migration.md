# Maps migration and deployment

`aos.patches.v1_0.harden_maps_subsystem` initializes invalid or missing Seller
`location_version` values in bounded batches and never commits internally.

Before migration, confirm TileServer, Nominatim and Valhalla data correspond to
the same approved Kenya extract. Deploy application code, run `bench migrate`,
clear cache and restart workers. Photon must remain disabled until its image is
approved, pinned and operationally monitored.

Recommended site configuration:

```bash
bench --site <site> set-config nominatim_base_url http://127.0.0.1:8081
bench --site <site> set-config valhalla_base_url http://127.0.0.1:8002
bench --site <site> set-config maps_geocoder_primary nominatim
bench --site <site> set-config maps_geocoder_fallback photon
bench --site <site> set-config maps_photon_enabled 0
```
