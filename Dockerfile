FROM registry.access.redhat.com/ubi9/python-312@sha256:83b01cf47b22e6ce98a0a4802772fb3d4b7e32280e3a1b7ffcd785e01956e1cb as gen_ref_indexes

WORKDIR /code

COPY ./dev-requirements/references.txt ./references.txt
COPY ./scripts/gen_ref_index.py ./gen_ref_index.py

RUN python -m pip install -r ./references.txt && \
    python ./gen_ref_index.py default --out-dir ./indexes

FROM registry.access.redhat.com/ubi9/python-312@sha256:83b01cf47b22e6ce98a0a4802772fb3d4b7e32280e3a1b7ffcd785e01956e1cb
WORKDIR /reinhard

COPY ./reinhard ./reinhard
COPY ./dev-requirements/constraints.txt ./requirements.txt
COPY ./main.py ./main.py
COPY --from=gen_ref_indexes /code/indexes ./indexes

ENV DOCKER_DEBUG=false
ENV REINHARD_INDEX_DIR=./indexes
RUN python -m pip install --no-cache-dir wheel && \
    python -m pip install --no-cache-dir -r requirements.txt

ENTRYPOINT if ${DOCKER_DEBUG} == false; then python main.py; else python -O main.py; fi
