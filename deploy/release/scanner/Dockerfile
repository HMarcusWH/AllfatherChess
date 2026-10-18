FROM scratch
COPY --chmod=0555 syft-scanner /bin/syft-scanner
ENV LOG_LEVEL=warn
ENTRYPOINT ["/bin/syft-scanner"]
