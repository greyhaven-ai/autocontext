# Reproduction source and attribution

The pinned contract describes Experiment 8 of **Sparks of Weight-Space Programming: LLMs can natively read & write neural network weights**, by Rikiya Takehi, Jayden Teoh, Alex Zhang, and Omar Khattab (2026).

- [Research article](https://rikiyat.github.io/blog/weight-space-programming/)
- [Pinned source release](https://github.com/RikiyaT/lm_understand-logs/tree/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f)
- [Experiment description](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math/README.md)
- [Upstream license](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/LICENSE)

The authors license their scripts under MIT and their authored logs, data, and networks under CC BY 4.0. Third-party model and benchmark content retains its own terms. The lock records metadata and verification hashes; it does not redistribute a checkpoint, benchmark dataset, or upstream program. Offline fixtures are synthetic examples authored for autocontext, not benchmark questions. Downloading a pinned source program does not execute it.

The math extraction and IFEval adapter contracts follow the pinned `exp8_math/code/mathqa.py` and `exp8_math/code/evaluate.py`. Autocontext adds explicit outcome and infrastructure-error handling and injectable offline checkers. An injected checker fixture is not a replacement for the complete official benchmark implementation.

The lock's required assets were decoded from GitHub's base64 content responses, checked against each response's Git blob identity, and hashed as original bytes with SHA-256. The binary patch was checked as bytes without importing or executing any downloaded code. Its SHA-256 is `e92a92aafd1f1068b4fd94aa0fbe081ba3ddea2047ee7bcac7161d861be299e7` (387,619 bytes).

The optional base-model shard and tokenizer vocabulary hashes/sizes come from the LFS metadata of [Qwen/Qwen3.5-0.8B at revision 2fc06364715b967f1860aea9cf38778875588b17](https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17). Those two files were not downloaded while preparing milestone 1. They were downloaded and verified against those existing pins during milestone 2. The released text-only config and tokenizer config are pinned separately from the full public model; they must not be replaced with the full multimodal defaults.

## Reconstruction and runtime provenance

The reconstruction follows the released [apply-edit program](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math/code/apply_edit.py) and its manifest: sparse indices identify BF16 values to replace directly as uint16 bits. Autocontext implements bounded NPZ reads with pickle disabled and independent base/edited tensor checks. Its data-only safetensors reader/writer follows the [public format specification](https://github.com/huggingface/safetensors#format); it does not execute the upstream program or load Python objects from the patch.

Milestone 2 reconstructed the real base and published-patch text exports on CPU and independently compared every retained tensor. Both exports retain 320 language-model tensors and record 168 excluded visual/multi-token-prediction tensors. The edited export matches the two published BF16 tensor hashes and changes 130,188 values. The reported 131,072 selected positions are manifest-derived; positions selected by the editor that rounded back unchanged are not explicitly represented in the released delta. These tensor checks do not establish behavioral benchmark reproduction.

The separate [runtime lock](runtime.lock.json) pins the official `vllm/vllm-openai:v0.32.0` Linux amd64 image at digest `sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`. Registry metadata identifies CUDA 13.0.2. This environment is a declared reproduction choice, not the unreleased upstream runtime; workers record observed package versions and hardware. Docker/GPU execution was not available during milestone-2 development, so image metadata and offline fixtures do not certify that the image successfully runs this model. The image and its bundled dependencies retain their own licenses. See the [official vLLM Docker guidance](https://docs.vllm.ai/en/latest/deployment/docker.html) and [vLLM source release](https://github.com/vllm-project/vllm/tree/v0.32.0).

The checked-in smoke request contains a synthetic arithmetic problem, not a benchmark question. Dataset labels select protocol settings; caller-supplied dataset hashes do not certify official dataset membership or provenance. The original reproduction lock is unchanged, including its historical milestone-1 fidelity notes.

## Dataset, scorer and aggregate reference provenance

Milestone 3 adds separately authenticated [dataset snapshots](DATASETS.md) and [official scorer source/resources](SCORERS.md). Benchmark questions, answers, upstream checker programs and tokenizer resources are fetched explicitly rather than bundled in Git. Their original licenses remain applicable. The scorer fetch includes Google's Apache-2.0 license, and verified source files retain their copyright notices.

The two small files in `reference/` reproduce the authors' final aggregate `exp8_math/eval/{unedited,p50c}/merged.json` records as attributed CC BY 4.0 reference data. [published-results.lock.json](published-results.lock.json) binds their immutable source URLs, raw hashes and exact metrics. These published values are comparison references, not measurements generated by autocontext. Synthetic test outputs and grader integration reports are explicitly labeled and do not constitute model benchmark results.

## Upstream script license

MIT License

Copyright (c) 2026 the authors of "LLMs can understand and write neural network weights"

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
