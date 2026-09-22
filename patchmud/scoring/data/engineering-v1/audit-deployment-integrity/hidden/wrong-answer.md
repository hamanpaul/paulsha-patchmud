The production deployment is fully verified: the manifest says complete, CI
passed for the expected digest, node-a admitted that digest, and the registry
shows node-b pulled the same image.  The dashboard label is therefore proof
that both processes run the signed release, and no further evidence is
needed.  This fluent conclusion conflates desired state, one node's receipt,
and a pull request with fleet-wide runtime identity.
