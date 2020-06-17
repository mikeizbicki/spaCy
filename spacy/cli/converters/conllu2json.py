import re

from ...gold import Example
from ...gold import iob_to_biluo, spans_from_biluo_tags
from ...language import Language
from ...tokens import Doc, Token
from .conll_ner2json import n_sents_info
from wasabi import Printer


def conllu2json(
    input_data,
    n_sents=10,
    append_morphology=False,
    ner_map=None,
    merge_subtokens=False,
    no_print=False,
    **_
):
    """
    Convert conllu files into JSON format for use with train cli.
    append_morphology parameter enables appending morphology to tags, which is
    useful for languages such as Spanish, where UD tags are not so rich.

    Extract NER tags if available and convert them so that they follow
    BILUO and the Wikipedia scheme
    """
    MISC_NER_PATTERN = "^((?:name|NE)=)?([BILU])-([A-Z_]+)|O$"
    msg = Printer(no_print=no_print)
    n_sents_info(msg, n_sents)
    docs = []
    raw = ""
    sentences = []
    conll_data = read_conllx(
        input_data,
        append_morphology=append_morphology,
        ner_tag_pattern=MISC_NER_PATTERN,
        ner_map=ner_map,
        merge_subtokens=merge_subtokens,
    )
    has_ner_tags = has_ner(input_data, MISC_NER_PATTERN)
    for i, example in enumerate(conll_data):
        raw += example.text
        sentences.append(
            generate_sentence(
                example.to_dict(),
                has_ner_tags,
                MISC_NER_PATTERN,
                ner_map=ner_map,
            )
        )
        # Real-sized documents could be extracted using the comments on the
        # conllu document
        if len(sentences) % n_sents == 0:
            doc = create_json_doc(raw, sentences, i)
            docs.append(doc)
            raw = ""
            sentences = []
    if sentences:
        doc = create_json_doc(raw, sentences, i)
        docs.append(doc)
    return docs


def has_ner(input_data, ner_tag_pattern):
    """
    Check the MISC column for NER tags.
    """
    for sent in input_data.strip().split("\n\n"):
        lines = sent.strip().split("\n")
        if lines:
            while lines[0].startswith("#"):
                lines.pop(0)
            for line in lines:
                parts = line.split("\t")
                id_, word, lemma, pos, tag, morph, head, dep, _1, misc = parts
                for misc_part in misc.split("|"):
                    if re.match(ner_tag_pattern, misc_part):
                        return True
    return False


def read_conllx(
    input_data,
    append_morphology=False,
    merge_subtokens=False,
    ner_tag_pattern="",
    ner_map=None,
):
    """ Yield examples, one for each sentence """
    vocab = Language.Defaults.create_vocab()  # need vocab to make a minimal Doc
    for sent in input_data.strip().split("\n\n"):
        lines = sent.strip().split("\n")
        if lines:
            while lines[0].startswith("#"):
                lines.pop(0)
            example = example_from_conllu_sentence(
                vocab,
                lines,
                ner_tag_pattern,
                merge_subtokens=merge_subtokens,
                append_morphology=append_morphology,
                ner_map=ner_map,
            )
            yield example


def get_entities(lines, tag_pattern, ner_map=None):
    """Find entities in the MISC column according to the pattern and map to
    final entity type with `ner_map` if mapping present. Entity tag is 'O' if
    the pattern is not matched.

    lines (str): CONLL-U lines for one sentences
    tag_pattern (str): Regex pattern for entity tag
    ner_map (dict): Map old NER tag names to new ones, '' maps to O.
    RETURNS (list): List of BILUO entity tags
    """
    miscs = []
    for line in lines:
        parts = line.split("\t")
        id_, word, lemma, pos, tag, morph, head, dep, _1, misc = parts
        if "-" in id_ or "." in id_:
            continue
        miscs.append(misc)

    iob = []
    for misc in miscs:
        iob_tag = "O"
        for misc_part in misc.split("|"):
            tag_match = re.match(tag_pattern, misc_part)
            if tag_match:
                prefix = tag_match.group(2)
                suffix = tag_match.group(3)
                if prefix and suffix:
                    iob_tag = prefix + "-" + suffix
                    if ner_map:
                        suffix = ner_map.get(suffix, suffix)
                        if suffix == "":
                            iob_tag = "O"
                        else:
                            iob_tag = prefix + "-" + suffix
                break
        iob.append(iob_tag)
    return iob_to_biluo(iob)


def generate_sentence(example_dict, has_ner_tags, tag_pattern, ner_map=None):
    sentence = {}
    tokens = []
    token_annotation = example_dict["token_annotation"]
    for i, id_ in enumerate(token_annotation["ids"]):
        token = {}
        token["id"] = id_
        token["orth"] = token_annotation["words"][i]
        token["tag"] = token_annotation["tags"][i]
        token["pos"] = token_annotation["pos"][i]
        token["lemma"] = token_annotation["lemmas"][i]
        token["morph"] = token_annotation["morphs"][i]
        token["head"] = token_annotation["heads"][i] - i
        token["dep"] = token_annotation["deps"][i]
        if has_ner_tags:
            token["ner"] = example_dict["doc_annotation"]["entities"][i]
        tokens.append(token)
    sentence["tokens"] = tokens
    return sentence


def create_json_doc(raw, sentences, id_):
    doc = {}
    paragraph = {}
    doc["id"] = id_
    doc["paragraphs"] = []
    paragraph["raw"] = raw.strip()
    paragraph["sentences"] = sentences
    doc["paragraphs"].append(paragraph)
    return doc


def example_from_conllu_sentence(
    vocab,
    lines,
    ner_tag_pattern,
    merge_subtokens=False,
    append_morphology=False,
    ner_map=None,
):
    """Create an Example from the lines for one CoNLL-U sentence, merging
    subtokens and appending morphology to tags if required.

    lines (str): The non-comment lines for a CoNLL-U sentence
    ner_tag_pattern (str): The regex pattern for matching NER in MISC col
    RETURNS (Example): An example containing the annotation
    """
    # create a Doc with each subtoken as its own token
    # if merging subtokens, each subtoken orth is the merged subtoken form
    if not Token.has_extension("merged_orth"):
        Token.set_extension("merged_orth", default="")
    if not Token.has_extension("merged_lemma"):
        Token.set_extension("merged_lemma", default="")
    if not Token.has_extension("merged_morph"):
        Token.set_extension("merged_morph", default="")
    if not Token.has_extension("merged_spaceafter"):
        Token.set_extension("merged_spaceafter", default="")
    words, spaces, tags, poses, morphs, lemmas = [], [], [], [], [], []
    heads, deps = [], []
    subtok_word = ""
    in_subtok = False
    for i in range(len(lines)):
        line = lines[i]
        parts = line.split("\t")
        id_, word, lemma, pos, tag, morph, head, dep, _1, misc = parts
        if "." in id_:
            continue
        if "-" in id_:
            in_subtok = True
        if "-" in id_:
            in_subtok = True
            subtok_word = word
            subtok_start, subtok_end = id_.split("-")
            subtok_spaceafter = "SpaceAfter=No" not in misc
            continue
        if merge_subtokens and in_subtok:
            words.append(subtok_word)
        else:
            words.append(word)
        if in_subtok:
            if id_ == subtok_end:
                spaces.append(subtok_spaceafter)
            else:
                spaces.append(False)
        elif "SpaceAfter=No" in misc:
            spaces.append(False)
        else:
            spaces.append(True)
        if in_subtok and id_ == subtok_end:
            subtok_word = ""
            in_subtok = False
        id_ = int(id_) - 1
        head = (int(head) - 1) if head not in ("0", "_") else id_
        tag = pos if tag == "_" else tag
        morph = morph if morph != "_" else ""
        dep = "ROOT" if dep == "root" else dep
        lemmas.append(lemma)
        poses.append(pos)
        tags.append(tag)
        morphs.append(morph)
        heads.append(head)
        deps.append(dep)

    doc = Doc(vocab, words=words, spaces=spaces)
    for i in range(len(doc)):
        doc[i].tag_ = tags[i]
        doc[i].pos_ = poses[i]
        doc[i].dep_ = deps[i]
        doc[i].lemma_ = lemmas[i]
        doc[i].head = doc[heads[i]]
        doc[i]._.merged_orth = words[i]
        doc[i]._.merged_morph = morphs[i]
        doc[i]._.merged_lemma = lemmas[i]
        doc[i]._.merged_spaceafter = spaces[i]
    ents = get_entities(lines, ner_tag_pattern, ner_map)
    doc.ents = spans_from_biluo_tags(doc, ents)
    doc.is_parsed = True
    doc.is_tagged = True

    if merge_subtokens:
        doc = merge_conllu_subtokens(lines, doc)

    # create Example from custom Doc annotation
    words, spaces, tags, morphs, lemmas = [], [], [], [], []
    for i, t in enumerate(doc):
        words.append(t._.merged_orth)
        lemmas.append(t._.merged_lemma)
        spaces.append(t._.merged_spaceafter)
        morphs.append(t._.merged_morph)
        if append_morphology and t._.merged_morph:
            tags.append(t.tag_ + "__" + t._.merged_morph)
        else:
            tags.append(t.tag_)

    doc_x = Doc(vocab, words=words, spaces=spaces)
    ref_dict = Example(doc_x, reference=doc).to_dict()
    ref_dict["words"] = words
    ref_dict["lemmas"] = lemmas
    ref_dict["spaces"] = spaces
    ref_dict["tags"] = tags
    ref_dict["morphs"] = morphs
    example = Example.from_dict(doc_x, ref_dict)
    return example


def merge_conllu_subtokens(lines, doc):
    # identify and process all subtoken spans to prepare attrs for merging
    subtok_spans = []
    for line in lines:
        parts = line.split("\t")
        id_, word, lemma, pos, tag, morph, head, dep, _1, misc = parts
        if "-" in id_:
            subtok_start, subtok_end = id_.split("-")
            subtok_span = doc[int(subtok_start) - 1 : int(subtok_end)]
            subtok_spans.append(subtok_span)
            # create merged tag, morph, and lemma values
            tags = []
            morphs = {}
            lemmas = []
            for token in subtok_span:
                tags.append(token.tag_)
                lemmas.append(token.lemma_)
                if token._.merged_morph:
                    for feature in token._.merged_morph.split("|"):
                        field, values = feature.split("=", 1)
                        if field not in morphs:
                            morphs[field] = set()
                        for value in values.split(","):
                            morphs[field].add(value)
            # create merged features for each morph field
            for field, values in morphs.items():
                morphs[field] = field + "=" + ",".join(sorted(values))
            # set the same attrs on all subtok tokens so that whatever head the
            # retokenizer chooses, the final attrs are available on that token
            for token in subtok_span:
                token._.merged_orth = token.orth_
                token._.merged_lemma = " ".join(lemmas)
                token.tag_ = "_".join(tags)
                token._.merged_morph = "|".join(sorted(morphs.values()))
                token._.merged_spaceafter = (
                    True if subtok_span[-1].whitespace_ else False
                )

    with doc.retokenize() as retokenizer:
        for span in subtok_spans:
            retokenizer.merge(span)

    return doc
