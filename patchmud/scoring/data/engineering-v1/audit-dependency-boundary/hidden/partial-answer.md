The lock file and CI report show telemetry-lib 3.7.2 and a patched scan, but
the deployment uses a different source commit and a tag without a digest.
The report asks for a runtime check, yet it still calls the advisory fixed
and suggests upgrading the dependency before the deployed package is known.
