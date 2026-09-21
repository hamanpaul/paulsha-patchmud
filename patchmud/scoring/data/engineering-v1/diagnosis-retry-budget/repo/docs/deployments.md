# Deployment notes

Version 2.4 changed the client timeout from four seconds to two seconds.  It
did not change the retry attempt limit or the delivery confirmation header.
Version 2.5 only updates logging around the confirmed delivery id.  The
incident report uses the word duplicate for two attempts, although the log
contains three distinct delivery ids and no repeated delivery confirmation.
