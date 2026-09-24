from __future__ import annotations

import os
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any

from .languages import normalize_language_code


@dataclass(frozen=True)
class TranslationConfig:
    model_path: str
    model_name: str
    device: str
    compute_type: str
    default_source_language: str
    max_chars: int
    max_source_tokens: int = 768
    max_decoding_length: int = 384
    beam_size: int = 4
    max_concurrent_requests: int = 4
    inter_threads: int = 2
    intra_threads: int = 0


@dataclass(frozen=True)
class TranslationResult:
    source_language: str
    target_language: str
    source_language_label: str
    target_language_label: str
    translated_content: str
    provider: str
    model_name: str


class TranslationError(Exception): pass
class ValidationError(TranslationError): pass
class BusyError(TranslationError): pass


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try: value=int(os.getenv(name,str(default)))
    except (TypeError,ValueError): value=default
    return max(low,min(high,value))


class TranslatorRuntime:
    """Process-level model runtime with bounded concurrent inference.

    CTranslate2 owns the shared translation engine. Tokenizers are thread-local
    because ``src_lang`` is mutable and must never race between requests.
    """
    def __init__(self, config: TranslationConfig):
        self.config=config
        self._translator: Any|None=None
        self._load_lock=threading.Lock()
        self._local=threading.local()
        self._slots=threading.BoundedSemaphore(max(1,config.max_concurrent_requests))

    def load(self) -> None:
        with self._load_lock:
            import ctranslate2
            from transformers import AutoTokenizer
            if self._translator is None:
                if not os.path.isdir(self.config.model_path): raise TranslationError("Translation model is unavailable.")
                kwargs={"device":self.config.device,"compute_type":self.config.compute_type,"inter_threads":self.config.inter_threads}
                if self.config.intra_threads>0: kwargs["intra_threads"]=self.config.intra_threads
                self._translator=ctranslate2.Translator(self.config.model_path,**kwargs)
            # Validate tokenizer files once during readiness/load without retaining
            # a shared mutable tokenizer instance.
            if not getattr(self._local,"validated",False):
                AutoTokenizer.from_pretrained(self.config.model_path,local_files_only=True)
                self._local.validated=True

    def _tokenizer(self):
        tokenizer=getattr(self._local,"tokenizer",None)
        if tokenizer is None:
            from transformers import AutoTokenizer
            tokenizer=AutoTokenizer.from_pretrained(self.config.model_path,local_files_only=True)
            self._local.tokenizer=tokenizer
        return tokenizer

    @property
    def is_loaded(self)->bool:return self._translator is not None

    def translate(self,*,text:str,target_language:str,source_language:str|None=None)->TranslationResult:
        clean=self._clean_text(text);target=normalize_language_code(target_language)
        if target is None: raise ValidationError("Unsupported target language.")
        source=normalize_language_code(source_language) if source_language else normalize_language_code(self.config.default_source_language)
        if source is None: raise ValidationError("Unsupported source language.")
        if source.code==target.code:
            return TranslationResult(source.code,target.code,source.label,target.label,clean,"nllb",self.config.model_name)
        if not self._slots.acquire(blocking=False): raise BusyError("Translation service is busy.")
        try:
            self.load(); tokenizer=self._tokenizer(); tokenizer.src_lang=source.code
            input_ids=tokenizer.encode(clean)
            if len(input_ids)>self.config.max_source_tokens: raise ValidationError("Text exceeds the translation token limit.")
            source_tokens=tokenizer.convert_ids_to_tokens(input_ids)
            result=self._translator.translate_batch([source_tokens],target_prefix=[[target.code]],beam_size=self.config.beam_size,
                max_decoding_length=self.config.max_decoding_length,repetition_penalty=1.1)
            output=result[0].hypotheses[0]
            if output and output[0]==target.code: output=output[1:]
            translated=tokenizer.decode(tokenizer.convert_tokens_to_ids(output),skip_special_tokens=True,clean_up_tokenization_spaces=True).strip()
            if not translated: raise TranslationError("Translation output was empty.")
            return TranslationResult(source.code,target.code,source.label,target.label,translated,"nllb",self.config.model_name)
        finally:self._slots.release()

    def _clean_text(self,text:str)->str:
        clean=unicodedata.normalize("NFC",str(text or "")).strip()
        if not clean: raise ValidationError("Text is required.")
        if "\x00" in clean: raise ValidationError("Text contains invalid characters.")
        if len(clean)>self.config.max_chars: raise ValidationError(f"Text cannot exceed {self.config.max_chars} characters.")
        return clean

_runtime: TranslatorRuntime|None=None
_runtime_lock=threading.Lock()

def get_config()->TranslationConfig:
    return TranslationConfig(
        model_path=os.getenv("TRANSLATION_MODEL_PATH","/models/nllb"),
        model_name=os.getenv("TRANSLATION_MODEL_NAME","nllb-200-distilled-1.3B-ct2-int8"),
        device=os.getenv("TRANSLATION_DEVICE","cpu"),compute_type=os.getenv("TRANSLATION_COMPUTE_TYPE","int8"),
        default_source_language=os.getenv("TRANSLATION_DEFAULT_SOURCE_LANGUAGE","eng_Latn"),
        max_chars=_env_int("TRANSLATION_MAX_CHARS",1000,1,5000),max_source_tokens=_env_int("TRANSLATION_MAX_SOURCE_TOKENS",768,32,2048),
        max_decoding_length=_env_int("TRANSLATION_MAX_DECODING_LENGTH",384,32,1024),beam_size=_env_int("TRANSLATION_BEAM_SIZE",4,1,8),
        max_concurrent_requests=_env_int("TRANSLATION_MAX_CONCURRENT_REQUESTS",4,1,32),inter_threads=_env_int("TRANSLATION_INTER_THREADS",2,1,16),
        intra_threads=_env_int("TRANSLATION_INTRA_THREADS",0,0,64),)

def get_runtime()->TranslatorRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:_runtime=TranslatorRuntime(get_config())
    return _runtime
