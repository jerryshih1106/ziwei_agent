---
base_model: answerdotai/ModernBERT-large
base_model_relation: finetune
datasets:
- sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1
language:
- en
library_name: sentence-transformers
metrics:
- cosine_accuracy
pipeline_tag: sentence-similarity
tags:
- sentence-transformers
- sentence-similarity
- feature-extraction
- generated_from_trainer
- dataset_size:11662655
- loss:CachedMultipleNegativesRankingLoss
widget:
- source_sentence: what county is lyndhurst, ohio in
  sentences:
  - This article is about the song written by Kenneth Gamble, Leon Huff and Cary Gilbert.
    For the Tina Turner song, see Don't Leave Me This Way (Tina Turner song). Don't
    Leave Me This Way is a song written by Kenneth Gamble, Leon Huff and Cary Gilbert.
    First charting as a hit for Harold Melvin & the Blue Notes featuring Teddy Pendergrass,
    an act on Gamble & Huff's Philadelphia International label in 1975, Don't Leave
    Me This Way was later a huge disco hit for Motown artist Thelma Houston in 1977.
  - "Lyndhurst is a city in Cuyahoga County, Ohio, United States. The population was\
    \ 14,001 at the 2010 census. Lyndhurst is located in northeastern Ohio, and is\
    \ a suburb of Cleveland. A small part of Lyndhurst was originally part of Mayfield\
    \ Township. It used to be called Euclidville before Lyndhurst was chosen. Lyndhurst\
    \ is located at 41Â°31â\x80²17â\x80³N 81Â°29â\x80²25â\x80³W / 41.52139Â°N 81.49028Â°W\
    \ / 41.52139; -81.49028 (41.521352, -81.490141)."
  - Welcome to Trumbull County... Trumbull County, the county seat, located in Warren,
    Ohio, consists of a combination of both urban and rural communities situated in
    the northeast corner of Ohio. It is situated roughly between the Youngstown, Cleveland
    and Akron corridors.
- source_sentence: who founded the american graphophone company
  sentences:
  - In 1886, Graham Bell and Charles Sumner Tainter founded the American Graphophone
    Company to distribute and sell graphophones in the US and Canada under license
    from the Volta Graphophone Company. In 1890, the American Graphophone Company
    stopped production of new phonographs due to sagging orders.
  - ShelfGenie How much does a ShelfGenie franchise cost? ShelfGenie has a franchise
    fee of up to $45,000, with a total initial investment range of $70,100 to $107,750.
    Local ShelfGenie franchise opportunities. ShelfGenie is looking to grow in a number
    of cities around the country. To find out if there's a franchise opportunity in
    your city, unlock more information.
  - "A+E Networks. The technology that made the modern music business possible came\
    \ into existence in the New Jersey laboratory where Thomas Alva Edison created\
    \ the first device to both record sound and play it back. He was awarded U.S.\
    \ Patent No. 200,521 for his inventionâ\x80\x93the phonographâ\x80\x93on this\
    \ day in 1878."
- source_sentence: is housekeeping camp flooded?
  sentences:
  - 'What is the importance of housekeeping at work? A: Workplace housekeeping promotes
    sanitation, safety, organization and productivity. It also boosts morale. Daily
    housekeeping maintenance keeps the workplac... Full Answer >'
  - The back patio area of a cabin is partially submerged in flood water at Housekeeping
    Camp on Monday, Jan. 9, 2017, in Yosemite National Park. The Merced River, swollen
    with storm runoff, crested at 12.7 feet at 4 a.m. SILVIA FLORES sflores@fresnobee.com.
  - "1 Bake for 8 minutes, then rotate the pan and check the underside of the bagels.\
    \ 2  If theyâ\x80\x99re getting too dark, place another pan under the baking sheet.\
    \ ( 3 Doubling the pan will insulate the first baking sheet.) Bake for another\
    \ 8 to 12 minutes, until the bagels are a golden brown. 4  13."
- source_sentence: causes for infection in the nerve of tooth
  sentences:
  - If a cavity is causing the toothache, your dentist will fill the cavity or possibly
    extract the tooth, if necessary. A root canal might be needed if the cause of
    the toothache is determined to be an infection of the tooth's nerve. Bacteria
    that have worked their way into the inner aspects of the tooth cause such an infection.
    An antibiotic may be prescribed if there is fever or swelling of the jaw.
  - "According to Article III, Section 1 of the Constitution, judges and justices\
    \ of the Judicial Branch serve during good behavior.. This means they are appointed\
    \ for life, unles â\x80¦ s they are impeached and removed from office. + 50 others\
    \ found this useful.he term length for members of the House are two years and\
    \ a staggering six years for members of the Senate."
  - Inflamed or infected pulp (pulpitis) most often causes a toothache. To relieve
    the pain and prevent further complications, the tooth may be extracted (surgically
    removed) or saved by root canal treatment.
- source_sentence: what county is hayden in
  sentences:
  - Normally, the Lead Agency is the agency with general governmental powers such
    as a city or a county. Agencies with limited powers or districts that provide
    a public service/utility such as a recreation and park district will tend to be
    a Responsible Agency.
  - According to the United States Census Bureau, the city has a total area of 9.61
    square miles (24.89 km2), of which 9.60 square miles (24.86 km2) is land and 0.01
    square miles (0.03 km2) is water. It lies at the southwestern end of Hayden Lake,
    and the elevation of the city is 2,287 feet (697 m) above sea level. Hayden is
    located on U.S. Route 95 at the junction of Route 41. It is also four miles (6
    km) north of Interstate 90 and Coeur d'Alene. The Coeur d'Alene airport is northwest
    of Hayden.
  - Hayden is a city in Kootenai County, Idaho, United States. Located in the northern
    portion of the state, just north of Coeur d'Alene, its population was 13,294 at
    the 2010 census.
model-index:
- name: SentenceTransformer based on answerdotai/ModernBERT-large
  results:
  - task:
      type: triplet
      name: Triplet
    dataset:
      name: msmarco co condenser dev
      type: msmarco-co-condenser-dev
    metrics:
    - type: cosine_accuracy
      value: 0.994
      name: Cosine Accuracy
  - task:
      type: retrieval
    dataset:
      name: SCIDOCS
      type: SCIDOCS
      split: test
    metrics:
      - type: ndcg@10
        value: 0.15789
  - task:
      type: retrieval
    dataset:
      name: FiQA2018
      type: FiQA2018
      split: test
    metrics:
      - type: ndcg@10
        value: 0.33974
  - task:
      type: retrieval
    dataset:
      name: HotpotQA
      type: HotpotQA
      split: test
    metrics:
      - type: ndcg@10
        value: 0.51818
  - task:
      type: retrieval
    dataset:
      name: ArguAna
      type: ArguAna
      split: test
    metrics:
      - type: ndcg@10
        value: 0.47797
  - task:
      type: retrieval
    dataset:
      name: NFCorpus
      type: NFCorpus
      split: test
    metrics:
      - type: ndcg@10
        value: 0.28443
  - task:
      type: retrieval
    dataset:
      name: SciFact
      type: SciFact
      split: test
    metrics:
      - type: ndcg@10
        value: 0.60626
  - task:
      type: retrieval
    dataset:
      name: TRECCOVID
      type: TRECCOVID
      split: test
    metrics:
      - type: ndcg@10
        value: 0.77495
---

# SentenceTransformer based on answerdotai/ModernBERT-large

This is a [sentence-transformers](https://www.SBERT.net) model finetuned from [answerdotai/ModernBERT-large](https://huggingface.co/answerdotai/ModernBERT-large) on the [msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1) dataset. It maps sentences & paragraphs to a 1024-dimensional dense vector space and can be used for semantic textual similarity, semantic search, paraphrase mining, text classification, clustering, and more.


I finetune ModernBERT-base using script from offical repo [train_st.py](https://github.com/AnswerDotAI/ModernBERT/blob/main/examples/train_st.py) on a RTX 4090 GPU with the only change of setting mini-batch size of `CachedMultipleNegativesRankingLoss` to 64. Training for 1 epoch takes less than 2 hours.

The mini-batch size of GradCache should not change model performnace, but the finetuned model performs better than that recorded in the paper. 

Training logs can be found here: https://api.wandb.ai/links/joe32140/ekuauaao.

## Model Details

### Model Description
- **Model Type:** Sentence Transformer
- **Base model:** [answerdotai/ModernBERT-large](https://huggingface.co/answerdotai/ModernBERT-large) <!-- at revision f87846cf8be76fceb18718f0245d18c8e6571215 -->
- **Maximum Sequence Length:** 8192 tokens
- **Output Dimensionality:** 1024 dimensions
- **Similarity Function:** Cosine Similarity
- **Training Dataset:**
    - [msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1)
- **Language:** en
<!-- - **License:** Unknown -->

### Model Sources

- **Documentation:** [Sentence Transformers Documentation](https://sbert.net)
- **Repository:** [Sentence Transformers on GitHub](https://github.com/UKPLab/sentence-transformers)
- **Hugging Face:** [Sentence Transformers on Hugging Face](https://huggingface.co/models?library=sentence-transformers)

### Full Model Architecture

```
SentenceTransformer(
  (0): Transformer({'max_seq_length': 8192, 'do_lower_case': False}) with Transformer model: ModernBertModel 
  (1): Pooling({'word_embedding_dimension': 1024, 'pooling_mode_cls_token': False, 'pooling_mode_mean_tokens': True, 'pooling_mode_max_tokens': False, 'pooling_mode_mean_sqrt_len_tokens': False, 'pooling_mode_weightedmean_tokens': False, 'pooling_mode_lasttoken': False, 'include_prompt': True})
)
```

## Usage

### Direct Usage (Sentence Transformers)

First install the Sentence Transformers library:

```bash
pip install -U sentence-transformers
```

Then you can load this model and run inference.
```python
from sentence_transformers import SentenceTransformer

# Download from the 🤗 Hub
model = SentenceTransformer("joe32140/ModernBERT-large-msmarco")
# Run inference
sentences = [
    'what county is hayden in',
    "Hayden is a city in Kootenai County, Idaho, United States. Located in the northern portion of the state, just north of Coeur d'Alene, its population was 13,294 at the 2010 census.",
    "According to the United States Census Bureau, the city has a total area of 9.61 square miles (24.89 km2), of which 9.60 square miles (24.86 km2) is land and 0.01 square miles (0.03 km2) is water. It lies at the southwestern end of Hayden Lake, and the elevation of the city is 2,287 feet (697 m) above sea level. Hayden is located on U.S. Route 95 at the junction of Route 41. It is also four miles (6 km) north of Interstate 90 and Coeur d'Alene. The Coeur d'Alene airport is northwest of Hayden.",
]
embeddings = model.encode(sentences)
print(embeddings.shape)
# [3, 1024]

# Get the similarity scores for the embeddings
similarities = model.similarity(embeddings, embeddings)
print(similarities.shape)
# [3, 3]
```

<!--
### Direct Usage (Transformers)

<details><summary>Click to see the direct usage in Transformers</summary>

</details>
-->

<!--
### Downstream Usage (Sentence Transformers)

You can finetune this model on your own dataset.

<details><summary>Click to expand</summary>

</details>
-->

<!--
### Out-of-Scope Use

*List how the model may foreseeably be misused and address what users ought not to do with the model.*
-->

## Evaluation

### Metrics

#### Triplet

* Dataset: `msmarco-co-condenser-dev`
* Evaluated with [<code>TripletEvaluator</code>](https://sbert.net/docs/package_reference/sentence_transformer/evaluation.html#sentence_transformers.evaluation.TripletEvaluator)

| Metric              | Value     |
|:--------------------|:----------|
| **cosine_accuracy** | **0.994** |

#### Retrieval tasks compared to original numbers in the paper

|               	| ModernBERT-base 	| ModernBERT-base (ours) 	| ModernBERT-large 	| ModernBERT-large (ours) 	|
|:------------------|------------------|-------------------------|-------------------|--------------------------|
|    NFCorpus   	| 23.7            	| 26.66                  	| 26.2             	| 28.44                   	|
|    SciFact    	| 57.0            	| 61.64                  	| 60.4             	| 63.66                   	|
|   TREC-Covid  	| 72.1            	| 71.43                  	| 74.1             	| 77.49                   	|
|      FiQA     	| 28.8            	| 30.73                  	| 33.1             	| 34.35                   	|
|    ArguAna    	| 35.7            	| 46.38                  	| 38.2             	| 47.79                   	|
|    SciDocs    	| 12.5            	| 13.67                  	| 13.8             	| 15.78                   	|
| FEVER         	| 59.9            	| 65.7                   	| 62.7             	| 68.2                    	|
| Climate-FEVER 	| 23.6            	| 22.6                   	| 20.5             	| 22.9                    	|
|   MLDR - OOD  	| 27.4            	| 30.58                  	| 34.3             	| 38.99                   	|
<!--
## Bias, Risks and Limitations

*What are the known or foreseeable issues stemming from this model? You could also flag here known failure cases or weaknesses of the model.*
-->

<!--
### Recommendations

*What are recommendations with respect to the foreseeable issues? For example, filtering explicit content.*
-->

## Training Details

### Training Dataset

#### msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1

* Dataset: [msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1) at [84ed2d3](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1/tree/84ed2d35626f617d890bd493b4d6db69a741e0e2)
* Size: 11,662,655 training samples
* Columns: <code>query</code>, <code>positive</code>, and <code>negative</code>
* Approximate statistics based on the first 1000 samples:
  |         | query                                                                            | positive                                                                            | negative                                                                            |
  |:--------|:---------------------------------------------------------------------------------|:------------------------------------------------------------------------------------|:------------------------------------------------------------------------------------|
  | type    | string                                                                           | string                                                                              | string                                                                              |
  | details | <ul><li>min: 4 tokens</li><li>mean: 9.26 tokens</li><li>max: 34 tokens</li></ul> | <ul><li>min: 17 tokens</li><li>mean: 79.14 tokens</li><li>max: 222 tokens</li></ul> | <ul><li>min: 24 tokens</li><li>mean: 80.09 tokens</li><li>max: 436 tokens</li></ul> |
* Samples:
  | query                                              | positive                                                                                                                                                                                                                                                                                                                               | negative                                                                                                                                                                                                                                                                                                                                                                                                                  |
  |:---------------------------------------------------|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
  | <code>what is the meaning of menu planning</code>  | <code>Menu planning is the selection of a menu for an event. Such as picking out the dinner for your wedding or even a meal at a Birthday Party. Menu planning is when you are preparing a calendar of meals and you have to sit down and decide what meat and veggies you want to serve on each certain day.</code>                   | <code>Menu Costs. In economics, a menu cost is the cost to a firm resulting from changing its prices. The name stems from the cost of restaurants literally printing new menus, but economists use it to refer to the costs of changing nominal prices in general.</code>                                                                                                                                                 |
  | <code>how old is brett butler</code>               | <code>Brett Butler is 59 years old. To be more precise (and nerdy), the current age as of right now is 21564 days or (even more geeky) 517536 hours. That's a lot of hours!</code>                                                                                                                                                     | <code>Passed in: St. John's, Newfoundland and Labrador, Canada. Passed on: 16/07/2016. Published in the St. John's Telegram. Passed away suddenly at the Health Sciences Centre surrounded by his loving family, on July 16, 2016 Robert (Bobby) Joseph Butler, age 52 years. Predeceased by his special aunt Geri Murrin and uncle Mike Mchugh; grandparents Joe and Margaret Murrin and Jack and Theresa Butler.</code> |
  | <code>when was the last navajo treaty sign?</code> | <code>In Executive Session, Senate of the United States, July 25, 1868. Resolved, (two-thirds of the senators present concurring,) That the Senate advise and consent to the ratification of the treaty between the United States and the Navajo Indians, concluded at Fort Sumner, New Mexico, on the first day of June, 1868.</code> | <code>Share Treaty of Greenville. The Treaty of Greenville was signed August 3, 1795, between the United States, represented by Gen. Anthony Wayne, and chiefs of the Indian tribes located in the Northwest Territory, including the Wyandots, Delawares, Shawnees, Ottawas, Miamis, and others.</code>                                                                                                                  |
* Loss: [<code>CachedMultipleNegativesRankingLoss</code>](https://sbert.net/docs/package_reference/sentence_transformer/losses.html#cachedmultiplenegativesrankingloss) with these parameters:
  ```json
  {
      "scale": 20.0,
      "similarity_fct": "cos_sim"
  }
  ```

### Evaluation Dataset

#### msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1

* Dataset: [msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1) at [84ed2d3](https://huggingface.co/datasets/sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1/tree/84ed2d35626f617d890bd493b4d6db69a741e0e2)
* Size: 11,662,655 evaluation samples
* Columns: <code>query</code>, <code>positive</code>, and <code>negative</code>
* Approximate statistics based on the first 1000 samples:
  |         | query                                                                           | positive                                                                            | negative                                                                            |
  |:--------|:--------------------------------------------------------------------------------|:------------------------------------------------------------------------------------|:------------------------------------------------------------------------------------|
  | type    | string                                                                          | string                                                                              | string                                                                              |
  | details | <ul><li>min: 4 tokens</li><li>mean: 9.2 tokens</li><li>max: 27 tokens</li></ul> | <ul><li>min: 21 tokens</li><li>mean: 80.44 tokens</li><li>max: 241 tokens</li></ul> | <ul><li>min: 23 tokens</li><li>mean: 80.38 tokens</li><li>max: 239 tokens</li></ul> |
* Samples:
  | query                                                 | positive                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | negative                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  |:------------------------------------------------------|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
  | <code>what county is holly springs nc in</code>       | <code>Holly Springs, North Carolina. Holly Springs is a town in Wake County, North Carolina, United States. As of the 2010 census, the town population was 24,661, over 2Â½ times its population in 2000. Contents.</code>                                                                                                                                                                                                                                                                                                                                                 | <code>The Mt. Holly Springs Park & Resort. One of the numerous trolley routes that carried people around the county at the turn of the century was the Carlisle & Mt. Holly Railway Company. The âHolly Trolleyâ as it came to be known was put into service by Patricio Russo and made its first run on May 14, 1901.</code>                                                                                                                                                                                                                                                 |
  | <code>how long does nyquil stay in your system</code> | <code>In order to understand exactly how long Nyquil lasts, it is absolutely vital to learn about the various ingredients in the drug. One of the ingredients found in Nyquil is Doxylamine, which is an antihistamine. This specific medication has a biological half-life or 6 to 12 hours. With this in mind, it is possible for the drug to remain in the system for a period of 12 to 24 hours. It should be known that the specifics will depend on a wide variety of different factors, including your age and metabolism.</code>                                   | <code>I confirmed that NyQuil is about 10% alcohol, a higher content than most domestic beers. When I asked about the relatively high proof, I was told that the alcohol dilutes the active ingredients. The alcohol free version is there for customers with addiction issues.. also found that in that version there is twice the amount of DXM. When I asked if I could speak to a chemist or scientist, I was told they didn't have anyone who fit that description there. Itâs been eight years since I kicked NyQuil. I've been sober from alcohol for four years.</code> |
  | <code>what are mineral water</code>                   | <code>1 Mineral water â water from a mineral spring that contains various minerals, such as salts and sulfur compounds. 2  It comes from a source tapped at one or more bore holes or spring, and originates from a geologically and physically protected underground water source. Mineral water â water from a mineral spring that contains various minerals, such as salts and sulfur compounds. 2  It comes from a source tapped at one or more bore holes or spring, and originates from a geologically and physically protected underground water source.</code> | <code>Minerals for Your Body. Drinking mineral water is beneficial to health and well-being. But it is not only the amount of water you drink that is important-what the water contains is even more essential.inerals for Your Body. Drinking mineral water is beneficial to health and well-being. But it is not only the amount of water you drink that is important-what the water contains is even more essential.</code>                                                                                                                                                    |
* Loss: [<code>CachedMultipleNegativesRankingLoss</code>](https://sbert.net/docs/package_reference/sentence_transformer/losses.html#cachedmultiplenegativesrankingloss) with these parameters:
  ```json
  {
      "scale": 20.0,
      "similarity_fct": "cos_sim"
  }
  ```

### Training Hyperparameters
#### Non-Default Hyperparameters

- `per_device_train_batch_size`: 512
- `per_device_eval_batch_size`: 512
- `learning_rate`: 0.0001
- `num_train_epochs`: 1
- `warmup_ratio`: 0.05
- `bf16`: True
- `batch_sampler`: no_duplicates

#### All Hyperparameters
<details><summary>Click to expand</summary>

- `overwrite_output_dir`: False
- `do_predict`: False
- `eval_strategy`: no
- `prediction_loss_only`: True
- `per_device_train_batch_size`: 512
- `per_device_eval_batch_size`: 512
- `per_gpu_train_batch_size`: None
- `per_gpu_eval_batch_size`: None
- `gradient_accumulation_steps`: 1
- `eval_accumulation_steps`: None
- `torch_empty_cache_steps`: None
- `learning_rate`: 0.0001
- `weight_decay`: 0.0
- `adam_beta1`: 0.9
- `adam_beta2`: 0.999
- `adam_epsilon`: 1e-08
- `max_grad_norm`: 1.0
- `num_train_epochs`: 1
- `max_steps`: -1
- `lr_scheduler_type`: linear
- `lr_scheduler_kwargs`: {}
- `warmup_ratio`: 0.05
- `warmup_steps`: 0
- `log_level`: passive
- `log_level_replica`: warning
- `log_on_each_node`: True
- `logging_nan_inf_filter`: True
- `save_safetensors`: True
- `save_on_each_node`: False
- `save_only_model`: False
- `restore_callback_states_from_checkpoint`: False
- `no_cuda`: False
- `use_cpu`: False
- `use_mps_device`: False
- `seed`: 42
- `data_seed`: None
- `jit_mode_eval`: False
- `use_ipex`: False
- `bf16`: True
- `fp16`: False
- `fp16_opt_level`: O1
- `half_precision_backend`: auto
- `bf16_full_eval`: False
- `fp16_full_eval`: False
- `tf32`: None
- `local_rank`: 0
- `ddp_backend`: None
- `tpu_num_cores`: None
- `tpu_metrics_debug`: False
- `debug`: []
- `dataloader_drop_last`: False
- `dataloader_num_workers`: 0
- `dataloader_prefetch_factor`: None
- `past_index`: -1
- `disable_tqdm`: False
- `remove_unused_columns`: True
- `label_names`: None
- `load_best_model_at_end`: False
- `ignore_data_skip`: False
- `fsdp`: []
- `fsdp_min_num_params`: 0
- `fsdp_config`: {'min_num_params': 0, 'xla': False, 'xla_fsdp_v2': False, 'xla_fsdp_grad_ckpt': False}
- `fsdp_transformer_layer_cls_to_wrap`: None
- `accelerator_config`: {'split_batches': False, 'dispatch_batches': None, 'even_batches': True, 'use_seedable_sampler': True, 'non_blocking': False, 'gradient_accumulation_kwargs': None}
- `deepspeed`: None
- `label_smoothing_factor`: 0.0
- `optim`: adamw_torch
- `optim_args`: None
- `adafactor`: False
- `group_by_length`: False
- `length_column_name`: length
- `ddp_find_unused_parameters`: None
- `ddp_bucket_cap_mb`: None
- `ddp_broadcast_buffers`: False
- `dataloader_pin_memory`: True
- `dataloader_persistent_workers`: False
- `skip_memory_metrics`: True
- `use_legacy_prediction_loop`: False
- `push_to_hub`: False
- `resume_from_checkpoint`: None
- `hub_model_id`: None
- `hub_strategy`: every_save
- `hub_private_repo`: None
- `hub_always_push`: False
- `gradient_checkpointing`: False
- `gradient_checkpointing_kwargs`: None
- `include_inputs_for_metrics`: False
- `include_for_metrics`: []
- `eval_do_concat_batches`: True
- `fp16_backend`: auto
- `push_to_hub_model_id`: None
- `push_to_hub_organization`: None
- `mp_parameters`: 
- `auto_find_batch_size`: False
- `full_determinism`: False
- `torchdynamo`: None
- `ray_scope`: last
- `ddp_timeout`: 1800
- `torch_compile`: False
- `torch_compile_backend`: None
- `torch_compile_mode`: None
- `dispatch_batches`: None
- `split_batches`: None
- `include_tokens_per_second`: False
- `include_num_input_tokens_seen`: False
- `neftune_noise_alpha`: None
- `optim_target_modules`: None
- `batch_eval_metrics`: False
- `eval_on_start`: False
- `use_liger_kernel`: False
- `eval_use_gather_object`: False
- `average_tokens_across_devices`: False
- `prompts`: None
- `batch_sampler`: no_duplicates
- `multi_dataset_batch_sampler`: proportional

</details>

### Training Logs
<details><summary>Click to expand</summary>

| Epoch  | Step | Training Loss | msmarco-co-condenser-dev_cosine_accuracy |
|:------:|:----:|:-------------:|:----------------------------------------:|
| 0      | 0    | -             | 0.599                                    |
| 0.0041 | 10   | 6.0983        | -                                        |
| 0.0082 | 20   | 4.4588        | -                                        |
| 0.0123 | 30   | 2.2492        | -                                        |
| 0.0164 | 40   | 0.9969        | -                                        |
| 0.0205 | 50   | 0.5272        | -                                        |
| 0.0246 | 60   | 0.3982        | -                                        |
| 0.0287 | 70   | 0.3335        | -                                        |
| 0.0328 | 80   | 0.3024        | -                                        |
| 0.0369 | 90   | 0.2932        | -                                        |
| 0.0410 | 100  | 0.2695        | -                                        |
| 0.0450 | 110  | 0.2574        | -                                        |
| 0.0491 | 120  | 0.2447        | -                                        |
| 0.0532 | 130  | 0.2491        | -                                        |
| 0.0573 | 140  | 0.2318        | -                                        |
| 0.0614 | 150  | 0.2292        | -                                        |
| 0.0655 | 160  | 0.2213        | -                                        |
| 0.0696 | 170  | 0.218         | -                                        |
| 0.0737 | 180  | 0.2234        | -                                        |
| 0.0778 | 190  | 0.2066        | -                                        |
| 0.0819 | 200  | 0.1987        | -                                        |
| 0.0860 | 210  | 0.1978        | -                                        |
| 0.0901 | 220  | 0.2024        | -                                        |
| 0.0942 | 230  | 0.1959        | -                                        |
| 0.0983 | 240  | 0.1804        | -                                        |
| 0.1024 | 250  | 0.1868        | -                                        |
| 0.1065 | 260  | 0.1983        | -                                        |
| 0.1106 | 270  | 0.1641        | -                                        |
| 0.1147 | 280  | 0.1713        | -                                        |
| 0.1188 | 290  | 0.1726        | -                                        |
| 0.1229 | 300  | 0.17          | -                                        |
| 0.1269 | 310  | 0.1783        | -                                        |
| 0.1310 | 320  | 0.1742        | -                                        |
| 0.1351 | 330  | 0.1654        | -                                        |
| 0.1392 | 340  | 0.1663        | -                                        |
| 0.1433 | 350  | 0.1616        | -                                        |
| 0.1474 | 360  | 0.157         | -                                        |
| 0.1515 | 370  | 0.1574        | -                                        |
| 0.1556 | 380  | 0.1529        | -                                        |
| 0.1597 | 390  | 0.1561        | -                                        |
| 0.1638 | 400  | 0.1435        | -                                        |
| 0.1679 | 410  | 0.1555        | -                                        |
| 0.1720 | 420  | 0.1455        | -                                        |
| 0.1761 | 430  | 0.1416        | -                                        |
| 0.1802 | 440  | 0.1407        | -                                        |
| 0.1843 | 450  | 0.138         | -                                        |
| 0.1884 | 460  | 0.1387        | -                                        |
| 0.1925 | 470  | 0.1499        | -                                        |
| 0.1966 | 480  | 0.1372        | -                                        |
| 0.2007 | 490  | 0.1308        | -                                        |
| 0.2048 | 500  | 0.1367        | -                                        |
| 0.2088 | 510  | 0.1324        | -                                        |
| 0.2129 | 520  | 0.1317        | -                                        |
| 0.2170 | 530  | 0.1263        | -                                        |
| 0.2211 | 540  | 0.1209        | -                                        |
| 0.2252 | 550  | 0.1201        | -                                        |
| 0.2293 | 560  | 0.1213        | -                                        |
| 0.2334 | 570  | 0.1329        | -                                        |
| 0.2375 | 580  | 0.1207        | -                                        |
| 0.2416 | 590  | 0.1211        | -                                        |
| 0.2457 | 600  | 0.1164        | -                                        |
| 0.2498 | 610  | 0.1292        | -                                        |
| 0.2539 | 620  | 0.1223        | -                                        |
| 0.2580 | 630  | 0.1237        | -                                        |
| 0.2621 | 640  | 0.1088        | -                                        |
| 0.2662 | 650  | 0.1196        | -                                        |
| 0.2703 | 660  | 0.1209        | -                                        |
| 0.2744 | 670  | 0.1155        | -                                        |
| 0.2785 | 680  | 0.1101        | -                                        |
| 0.2826 | 690  | 0.1127        | -                                        |
| 0.2867 | 700  | 0.1082        | -                                        |
| 0.2907 | 710  | 0.1083        | -                                        |
| 0.2948 | 720  | 0.1132        | -                                        |
| 0.2989 | 730  | 0.1121        | -                                        |
| 0.3030 | 740  | 0.1146        | -                                        |
| 0.3071 | 750  | 0.1088        | -                                        |
| 0.3112 | 760  | 0.0982        | -                                        |
| 0.3153 | 770  | 0.0952        | -                                        |
| 0.3194 | 780  | 0.1034        | -                                        |
| 0.3235 | 790  | 0.1017        | -                                        |
| 0.3276 | 800  | 0.1016        | -                                        |
| 0.3317 | 810  | 0.1054        | -                                        |
| 0.3358 | 820  | 0.1003        | -                                        |
| 0.3399 | 830  | 0.0932        | -                                        |
| 0.3440 | 840  | 0.0997        | -                                        |
| 0.3481 | 850  | 0.0921        | -                                        |
| 0.3522 | 860  | 0.0958        | -                                        |
| 0.3563 | 870  | 0.0973        | -                                        |
| 0.3604 | 880  | 0.0931        | -                                        |
| 0.3645 | 890  | 0.0964        | -                                        |
| 0.3686 | 900  | 0.0982        | -                                        |
| 0.3726 | 910  | 0.0908        | -                                        |
| 0.3767 | 920  | 0.0917        | -                                        |
| 0.3808 | 930  | 0.0857        | -                                        |
| 0.3849 | 940  | 0.0925        | -                                        |
| 0.3890 | 950  | 0.0915        | -                                        |
| 0.3931 | 960  | 0.089         | -                                        |
| 0.3972 | 970  | 0.0876        | -                                        |
| 0.4013 | 980  | 0.0959        | -                                        |
| 0.4054 | 990  | 0.0879        | -                                        |
| 0.4095 | 1000 | 0.0883        | -                                        |
| 0.4136 | 1010 | 0.0824        | -                                        |
| 0.4177 | 1020 | 0.0897        | -                                        |
| 0.4218 | 1030 | 0.0954        | -                                        |
| 0.4259 | 1040 | 0.0815        | -                                        |
| 0.4300 | 1050 | 0.0806        | -                                        |
| 0.4341 | 1060 | 0.0918        | -                                        |
| 0.4382 | 1070 | 0.0851        | -                                        |
| 0.4423 | 1080 | 0.0888        | -                                        |
| 0.4464 | 1090 | 0.0863        | -                                        |
| 0.4505 | 1100 | 0.0856        | -                                        |
| 0.4545 | 1110 | 0.0809        | -                                        |
| 0.4586 | 1120 | 0.085         | -                                        |
| 0.4627 | 1130 | 0.0756        | -                                        |
| 0.4668 | 1140 | 0.0836        | -                                        |
| 0.4709 | 1150 | 0.0815        | -                                        |
| 0.4750 | 1160 | 0.084         | -                                        |
| 0.4791 | 1170 | 0.0751        | -                                        |
| 0.4832 | 1180 | 0.0794        | -                                        |
| 0.4873 | 1190 | 0.0844        | -                                        |
| 0.4914 | 1200 | 0.0835        | -                                        |
| 0.4955 | 1210 | 0.0798        | -                                        |
| 0.4996 | 1220 | 0.0825        | -                                        |
| 0.5037 | 1230 | 0.0796        | -                                        |
| 0.5078 | 1240 | 0.0758        | -                                        |
| 0.5119 | 1250 | 0.0765        | -                                        |
| 0.5160 | 1260 | 0.0806        | -                                        |
| 0.5201 | 1270 | 0.072         | -                                        |
| 0.5242 | 1280 | 0.0775        | -                                        |
| 0.5283 | 1290 | 0.076         | -                                        |
| 0.5324 | 1300 | 0.0767        | -                                        |
| 0.5364 | 1310 | 0.0782        | -                                        |
| 0.5405 | 1320 | 0.07          | -                                        |
| 0.5446 | 1330 | 0.0724        | -                                        |
| 0.5487 | 1340 | 0.0703        | -                                        |
| 0.5528 | 1350 | 0.072         | -                                        |
| 0.5569 | 1360 | 0.0763        | -                                        |
| 0.5610 | 1370 | 0.0703        | -                                        |
| 0.5651 | 1380 | 0.0688        | -                                        |
| 0.5692 | 1390 | 0.0703        | -                                        |
| 0.5733 | 1400 | 0.0659        | -                                        |
| 0.5774 | 1410 | 0.0688        | -                                        |
| 0.5815 | 1420 | 0.0713        | -                                        |
| 0.5856 | 1430 | 0.0722        | -                                        |
| 0.5897 | 1440 | 0.0682        | -                                        |
| 0.5938 | 1450 | 0.07          | -                                        |
| 0.5979 | 1460 | 0.0649        | -                                        |
| 0.6020 | 1470 | 0.0659        | -                                        |
| 0.6061 | 1480 | 0.0675        | -                                        |
| 0.6102 | 1490 | 0.0629        | -                                        |
| 0.6143 | 1500 | 0.0683        | -                                        |
| 0.6183 | 1510 | 0.0687        | -                                        |
| 0.6224 | 1520 | 0.0724        | -                                        |
| 0.6265 | 1530 | 0.0638        | -                                        |
| 0.6306 | 1540 | 0.0709        | -                                        |
| 0.6347 | 1550 | 0.064         | -                                        |
| 0.6388 | 1560 | 0.0646        | -                                        |
| 0.6429 | 1570 | 0.0673        | -                                        |
| 0.6470 | 1580 | 0.0607        | -                                        |
| 0.6511 | 1590 | 0.0671        | -                                        |
| 0.6552 | 1600 | 0.0627        | -                                        |
| 0.6593 | 1610 | 0.0644        | -                                        |
| 0.6634 | 1620 | 0.0629        | -                                        |
| 0.6675 | 1630 | 0.0656        | -                                        |
| 0.6716 | 1640 | 0.0633        | -                                        |
| 0.6757 | 1650 | 0.062         | -                                        |
| 0.6798 | 1660 | 0.0627        | -                                        |
| 0.6839 | 1670 | 0.0583        | -                                        |
| 0.6880 | 1680 | 0.0612        | -                                        |
| 0.6921 | 1690 | 0.066         | -                                        |
| 0.6962 | 1700 | 0.0645        | -                                        |
| 0.7002 | 1710 | 0.0599        | -                                        |
| 0.7043 | 1720 | 0.0552        | -                                        |
| 0.7084 | 1730 | 0.065         | -                                        |
| 0.7125 | 1740 | 0.0614        | -                                        |
| 0.7166 | 1750 | 0.0615        | -                                        |
| 0.7207 | 1760 | 0.0567        | -                                        |
| 0.7248 | 1770 | 0.0528        | -                                        |
| 0.7289 | 1780 | 0.0541        | -                                        |
| 0.7330 | 1790 | 0.0548        | -                                        |
| 0.7371 | 1800 | 0.0568        | -                                        |
| 0.7412 | 1810 | 0.053         | -                                        |
| 0.7453 | 1820 | 0.0603        | -                                        |
| 0.7494 | 1830 | 0.0594        | -                                        |
| 0.7535 | 1840 | 0.0549        | -                                        |
| 0.7576 | 1850 | 0.0601        | -                                        |
| 0.7617 | 1860 | 0.0604        | -                                        |
| 0.7658 | 1870 | 0.0524        | -                                        |
| 0.7699 | 1880 | 0.057         | -                                        |
| 0.7740 | 1890 | 0.057         | -                                        |
| 0.7781 | 1900 | 0.0551        | -                                        |
| 0.7821 | 1910 | 0.0574        | -                                        |
| 0.7862 | 1920 | 0.0555        | -                                        |
| 0.7903 | 1930 | 0.0564        | -                                        |
| 0.7944 | 1940 | 0.052         | -                                        |
| 0.7985 | 1950 | 0.054         | -                                        |
| 0.8026 | 1960 | 0.0573        | -                                        |
| 0.8067 | 1970 | 0.056         | -                                        |
| 0.8108 | 1980 | 0.0503        | -                                        |
| 0.8149 | 1990 | 0.0525        | -                                        |
| 0.8190 | 2000 | 0.0505        | -                                        |
| 0.8231 | 2010 | 0.0547        | -                                        |
| 0.8272 | 2020 | 0.0531        | -                                        |
| 0.8313 | 2030 | 0.0534        | -                                        |
| 0.8354 | 2040 | 0.0542        | -                                        |
| 0.8395 | 2050 | 0.0536        | -                                        |
| 0.8436 | 2060 | 0.0512        | -                                        |
| 0.8477 | 2070 | 0.0508        | -                                        |
| 0.8518 | 2080 | 0.0517        | -                                        |
| 0.8559 | 2090 | 0.0516        | -                                        |
| 0.8600 | 2100 | 0.0558        | -                                        |
| 0.8640 | 2110 | 0.0571        | -                                        |
| 0.8681 | 2120 | 0.0536        | -                                        |
| 0.8722 | 2130 | 0.0561        | -                                        |
| 0.8763 | 2140 | 0.0489        | -                                        |
| 0.8804 | 2150 | 0.0513        | -                                        |
| 0.8845 | 2160 | 0.0455        | -                                        |
| 0.8886 | 2170 | 0.0479        | -                                        |
| 0.8927 | 2180 | 0.0498        | -                                        |
| 0.8968 | 2190 | 0.0523        | -                                        |
| 0.9009 | 2200 | 0.0513        | -                                        |
| 0.9050 | 2210 | 0.049         | -                                        |
| 0.9091 | 2220 | 0.0504        | -                                        |
| 0.9132 | 2230 | 0.0462        | -                                        |
| 0.9173 | 2240 | 0.0469        | -                                        |
| 0.9214 | 2250 | 0.0501        | -                                        |
| 0.9255 | 2260 | 0.046         | -                                        |
| 0.9296 | 2270 | 0.0475        | -                                        |
| 0.9337 | 2280 | 0.0504        | -                                        |
| 0.9378 | 2290 | 0.0483        | -                                        |
| 0.9419 | 2300 | 0.0536        | -                                        |
| 0.9459 | 2310 | 0.0442        | -                                        |
| 0.9500 | 2320 | 0.0499        | -                                        |
| 0.9541 | 2330 | 0.0478        | -                                        |
| 0.9582 | 2340 | 0.0499        | -                                        |
| 0.9623 | 2350 | 0.048         | -                                        |
| 0.9664 | 2360 | 0.0451        | -                                        |
| 0.9705 | 2370 | 0.0501        | -                                        |
| 0.9746 | 2380 | 0.0464        | -                                        |
| 0.9787 | 2390 | 0.0451        | -                                        |
| 0.9828 | 2400 | 0.0413        | -                                        |
| 0.9869 | 2410 | 0.0478        | -                                        |
| 0.9910 | 2420 | 0.0466        | -                                        |
| 0.9951 | 2430 | 0.0515        | -                                        |
| 0.9992 | 2440 | 0.0484        | -                                        |
| 1.0    | 2442 | -             | 0.994                                    |

</details>

### Framework Versions
- Python: 3.11.9
- Sentence Transformers: 3.3.0
- Transformers: 4.48.0.dev0
- PyTorch: 2.4.0
- Accelerate: 1.2.1
- Datasets: 2.21.0
- Tokenizers: 0.21.0

## Citation

### BibTeX

#### Sentence Transformers
```bibtex
@inproceedings{reimers-2019-sentence-bert,
    title = "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks",
    author = "Reimers, Nils and Gurevych, Iryna",
    booktitle = "Proceedings of the 2019 Conference on Empirical Methods in Natural Language Processing",
    month = "11",
    year = "2019",
    publisher = "Association for Computational Linguistics",
    url = "https://arxiv.org/abs/1908.10084",
}
```

#### CachedMultipleNegativesRankingLoss
```bibtex
@misc{gao2021scaling,
    title={Scaling Deep Contrastive Learning Batch Size under Memory Limited Setup},
    author={Luyu Gao and Yunyi Zhang and Jiawei Han and Jamie Callan},
    year={2021},
    eprint={2101.06983},
    archivePrefix={arXiv},
    primaryClass={cs.LG}
}
```

<!--
## Glossary

*Clearly define terms in order to be accessible across audiences.*
-->

<!--
## Model Card Authors

*Lists the people who create the model card, providing recognition and accountability for the detailed work that goes into its construction.*
-->

<!--
## Model Card Contact

*Provides a way for people who have updates to the Model Card, suggestions, or questions, to contact the Model Card authors.*
-->